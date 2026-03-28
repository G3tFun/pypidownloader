import asyncio
import httpx
import re
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse

app = FastAPI()

# Список 5-ти гигантов для "гонки" за самим файлом (.whl, .tar.gz)
MIRROR_FILES = [
    "https://mirror.yandex.ru/pypi/packages",
    "https://pypi.tuna.tsinghua.edu.cn/packages",
    "https://mirrors.aliyun.com/pypi/packages",
    "https://repo.huaweicloud.com/repository/pypi/packages",
    "https://files.pythonhosted.org/packages"
]

# Список доменов для замены в HTML (чтобы перехватить ссылки и отправить их в наш прокси)
DOMAINS_TO_REPLACE = [
    r"https://files.pythonhosted.org/packages",
    r"https://mirror.yandex.ru/pypi/packages",
    r"https://pypi.tuna.tsinghua.edu.cn/packages",
    r"https://mirrors.aliyun.com/pypi/packages",
    r"https://repo.huaweicloud.com/repository/pypi/packages",
    r"https://[a-zA-Z0-9.-]+/pypi/packages",
    r"https://[a-zA-Z0-9.-]+/packages"
]

@app.get("/")
async def root():
    return HTMLResponse("<h1>🌐 Smart PyPI Proxy is Live!</h1><p>Use: <code>-i https://pypi-cdn.vercel.app/simple</code></p>")

@app.api_route("/simple/{package}/{rest:path}", methods=["GET", "HEAD"])
@app.api_route("/simple/{package}", methods=["GET", "HEAD"])
async def proxy_simple(package: str, rest: str = ""):
    """
    Получает список версий с официального PyPI (там есть всё, даже старье).
    Затем подменяет ссылки на наш прокси /packages/...
    """
    headers = {"User-Agent": "pip/24.0"}
    # Используем pypi.org как эталон списка версий
    target_url = f"https://pypi.org/simple/{package}/"
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        try:
            resp = await client.get(target_url, headers=headers)
            
            if resp.status_code == 200:
                content = resp.text
                
                # 1. Сначала чистим относительные пути, если они есть
                content = content.replace("../../packages", "/packages")
                content = content.replace("../packages", "/packages")
                
                # 2. Перехватываем все ссылки на хранилища файлов и меняем на наш /packages
                for domain in DOMAINS_TO_REPLACE:
                    content = re.sub(domain, "/packages", content)
                
                return HTMLResponse(content=content)
            
            return HTMLResponse(f"Package {package} not found on PyPI", status_code=404)
        except Exception as e:
            return HTMLResponse(f"Error fetching simple index: {str(e)}", status_code=500)

async def find_fastest_mirror(path: str):
    """Опрашивает зеркала и выбирает самое быстрое для скачивания файла."""
    async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as client:
        tasks = [client.head(f"{mirror}/{path}") for mirror in MIRROR_FILES]
        
        try:
            # Возвращаем первое зеркало, которое ответило 200 OK
            for completed_task in asyncio.as_completed(tasks, timeout=3.5):
                try:
                    res = await completed_task
                    if res.status_code == 200:
                        return str(res.url)
                except Exception:
                    continue 
        except Exception:
            pass

    # Если никто не ответил вовремя, используем Яндекс по умолчанию
    return f"{MIRROR_FILES[0]}/{path}"

@app.api_route("/packages/{path:path}", methods=["GET", "HEAD"])
async def proxy_packages(path: str):
    """Точка входа для скачивания файлов: запускает гонку и делает редирект."""
    fastest_url = await find_fastest_mirror(path)
    return RedirectResponse(url=fastest_url, status_code=302)
