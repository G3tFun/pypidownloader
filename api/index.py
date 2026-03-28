import asyncio
import httpx
import re
from fastapi import FastAPI
from fastapi.responses import RedirectResponse, HTMLResponse

app = FastAPI(title="Smart PyPI Proxy")

# Зеркала для скачивания самих файлов (.whl, .tar.gz)
MIRROR_FILES = [
    "https://mirror.yandex.ru/pypi/packages",
    "https://pypi.tuna.tsinghua.edu.cn/packages",
    "https://mirrors.aliyun.com/pypi/packages",
    "https://repo.huaweicloud.com/repository/pypi/packages",
    "https://files.pythonhosted.org/packages" # Официальный хостинг участвует в гонке
]

# Список доменов для замены в HTML на наш прокси (компилируем заранее для скорости)
DOMAINS_TO_REPLACE = [
    re.compile(r"https://files\.pythonhosted\.org/packages"),
    re.compile(r"https://mirror\.yandex\.ru/pypi/packages"),
    re.compile(r"https://pypi\.tuna\.tsinghua\.edu\.cn/packages"),
    re.compile(r"https://mirrors\.aliyun\.com/pypi/packages"),
    re.compile(r"https://repo\.huaweicloud\.com/repository/pypi/packages"),
    re.compile(r"https://[a-zA-Z0-9.-]+/pypi/packages"),
    re.compile(r"https://[a-zA-Z0-9.-]+/packages")
]

@app.get("/")
async def root():
    return HTMLResponse(
        "<h1>🌐 Smart PyPI Proxy is Live. Use it!</h1>"
        "<p>Use: <code>pip install &lt;package&gt; -i https://pypi-cdn.vercel.app/simple</code></p>"
    )

@app.api_route("/simple/{package}/{rest:path}", methods=["GET", "HEAD"])
@app.api_route("/simple/{package}", methods=["GET", "HEAD"])
async def proxy_simple(package: str, rest: str = ""):
    """Улучшенная версия с нормализацией имен пакетов."""
    headers = {"User-Agent": "pip/24.0", "Accept": "text/html"}
    
    # 1. Нормализуем имя (PyPI просит заменять _ на - в URL)
    safe_package = package.replace("_", "-").lower()
    target_url = f"https://pypi.org/simple/{safe_package}/"
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        try:
            resp = await client.get(target_url, headers=headers)
            
            # 2. Если по дефису не нашли, пробуем оригинал
            if resp.status_code != 200:
                resp = await client.get(f"https://pypi.org/simple/{package}/", headers=headers)
            
            if resp.status_code == 200:
                content = resp.text
                
                # 3. Заменяем относительные пути
                content = content.replace("../../packages", "/packages")
                content = content.replace("../packages", "/packages")
                
                # 4. Заменяем абсолютные ссылки на наш локальный /packages/
                for domain_regex in DOMAINS_TO_REPLACE:
                    content = domain_regex.sub("/packages", content)
                
                return HTMLResponse(content=content)
            
            return HTMLResponse(f"Package {package} not found", status_code=404)
        except Exception as e:
            return HTMLResponse(f"Error fetching simple index: {str(e)}", status_code=500)

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
