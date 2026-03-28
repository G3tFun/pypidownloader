import asyncio
import httpx
import re
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse, Response

app = FastAPI(title="Smart PyPI Proxy")

# Зеркала для скачивания самих файлов (.whl, .tar.gz)
MIRROR_FILES = [
    "https://mirror.yandex.ru/pypi/packages",
    "https://pypi.tuna.tsinghua.edu.cn/packages",
    "https://mirrors.aliyun.com/pypi/packages",
    "https://repo.huaweicloud.com/repository/pypi/packages",
    "https://files.pythonhosted.org/packages" # Официальный хостинг участвует в гонке
]

# Единая регулярка, которая покрывает оригинальный PyPI и любые зеркала с подкаталогами 
# (например, /pypi/packages или /repository/pypi/packages)
DOMAINS_TO_REPLACE = [
    re.compile(r"https?://[a-zA-Z0-9.-]+(?:/[a-zA-Z0-9.-]+)*/packages")
]

@app.get("/")
async def root():
    return HTMLResponse(
        "<h1>🌐 Smart PyPI Proxy is Live</h1>"
        "<p>Use: <code>pip install &lt;package&gt; -i https://pypi-cdn.vercel.app/simple</code></p>"
    )

@app.api_route("/simple/{package}/", methods=["GET", "HEAD"])
@app.api_route("/simple/{package}", methods=["GET", "HEAD"])
async def proxy_simple(package: str, request: Request):
    """Улучшенная версия с fallback на зеркала и поддержкой PEP 691 JSON API."""
    
    # 1. Нормализуем имя (PyPI просит заменять _ на - в URL)
    safe_package = package.replace("_", "-").lower()
    
    # Пробрасываем Accept от pip, чтобы получать JSON, если pip его запросил (для новых pip)
    client_accept = request.headers.get("Accept", "text/html")
    headers = {"User-Agent": "pip/24.0", "Accept": client_accept}
    
    # Зеркала для получения самого списка версий. PyPI может блокировать (403 Cloudflare) IP Vercel.
    index_mirrors = [
        f"https://pypi.org/simple/{safe_package}/",
        f"https://pypi.tuna.tsinghua.edu.cn/simple/{safe_package}/",
        f"https://mirror.yandex.ru/pypi/simple/{safe_package}/"
    ]
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
        for url in index_mirrors:
            try:
                resp = await client.get(url, headers=headers)
                
                # Если получаем 200 OK, значит страница найдена (избегаем Cloudflare 403)
                if resp.status_code == 200:
                    content = resp.text
                    
                    # 2. Заменяем относительные пути (если PyPI отдал старый HTML)
                    content = content.replace("../../packages", "/packages")
                    content = content.replace("../packages", "/packages")
                    
                    # 3. Заменяем абсолютные ссылки на наш локальный /packages/
                    for domain_regex in DOMAINS_TO_REPLACE:
                        content = domain_regex.sub("/packages", content)
                    
                    # Возвращаем с оригинальным Content-Type (text/html или application/vnd.pypi.simple.v1+json)
                    content_type = resp.headers.get("Content-Type", "text/html")
                    return Response(content=content, media_type=content_type)
            except Exception:
                continue # Если зеркало упало или таймаут, пробуем следующее
        
        # 4. Если по дефису не нашли ни на одном зеркале, пробуем оригинал (редкий случай)
        if safe_package != package:
            fallback_url = f"https://pypi.org/simple/{package}/"
            try:
                resp = await client.get(fallback_url, headers=headers)
                if resp.status_code == 200:
                    content = resp.text
                    content = content.replace("../../packages", "/packages")
                    content = content.replace("../packages", "/packages")
                    for domain_regex in DOMAINS_TO_REPLACE:
                        content = domain_regex.sub("/packages", content)
                    content_type = resp.headers.get("Content-Type", "text/html")
                    return Response(content=content, media_type=content_type)
            except Exception:
                pass

    return HTMLResponse(f"Package {package} not found on any mirror", status_code=404)

async def find_fastest_mirror(path: str):
    """Гонка за файлом: кто быстрее отдаст 200 OK."""
    async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as client:
        tasks = [client.head(f"{mirror}/{path}") for mirror in MIRROR_FILES]
        
        try:
            # Ждем первый успешный ответ (гонка запросов)
            for completed_task in asyncio.as_completed(tasks, timeout=3.5):
                try:
                    res = await completed_task
                    # Отдаем ссылку только если зеркало реально имеет этот файл (200 OK)
                    if res.status_code == 200:
                        return str(res.url)
                except Exception:
                    continue 
        except Exception:
            pass # Игнорируем общий таймаут гонки

    # План Б: Если все зеркала молчат (или файл удален с зеркал), 
    # жестко отдаем с официального pythonhosted
    return f"https://files.pythonhosted.org/packages/{path}"

@app.api_route("/packages/{path:path}", methods=["GET", "HEAD"])
async def proxy_packages(path: str):
    """Перехватываем запрос на файл и редиректим на лучшее зеркало."""
    fastest_url = await find_fastest_mirror(path)
    return RedirectResponse(url=fastest_url, status_code=302)
