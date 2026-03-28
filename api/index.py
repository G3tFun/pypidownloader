import asyncio
import httpx
import re
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse

app = FastAPI()

# Список 5-ти гигантов для "гонки" за файлом
MIRROR_FILES = [
    "https://mirror.yandex.ru/pypi/packages",
    "https://pypi.tuna.tsinghua.edu.cn/packages",
    "https://mirrors.aliyun.com/pypi/packages",
    "https://repo.huaweicloud.com/repository/pypi/packages",
    "https://files.pythonhosted.org/packages"
]

# Список доменов для перехвата в HTML
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
    return HTMLResponse("<h1>🌐 Smart PyPI Proxy is Live</h1><p>Use: <code>-i https://pypi-cdn.vercel.app/simple</code></p>")

@app.api_route("/simple/{package}/{rest:path}", methods=["GET", "HEAD"])
@app.api_route("/simple/{package}", methods=["GET", "HEAD"])
async def proxy_simple(package: str, rest: str = ""):
    """Получает список пакетов, делает ссылки абсолютными и локальными."""
    headers = {"User-Agent": "pip/24.0"}
    # Берем список из Яндекса (самый стабильный)
    target_url = f"https://mirror.yandex.ru/pypi/simple/{package}/"
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
        try:
            resp = await client.get(target_url, headers=headers)
            if resp.status_code != 200:
                # Резерв — Китай
                resp = await client.get(f"https://pypi.tuna.tsinghua.edu.cn/simple/{package}/", headers=headers)
            
            if resp.status_code == 200:
                content = resp.text
                
                # 1. Сначала превращаем относительные пути ../../ в абсолютные /packages/
                content = content.replace("../../packages", "/packages")
                content = content.replace("../packages", "/packages")
                
                # 2. Перехватываем все внешние домены зеркала
                for domain in DOMAINS_TO_REPLACE:
                    content = re.sub(domain, "/packages", content)
                
                return HTMLResponse(content=content)
            
            return HTMLResponse(f"Package {package} not found", status_code=404)
        except Exception as e:
            return HTMLResponse(f"Error: {str(e)}", status_code=500)

async def find_fastest_mirror(path: str):
    """Та самая 'гонка' зеркал за 0.1-0.5 сек."""
    async with httpx.AsyncClient(timeout=2.0) as client:
        # HEAD запросы ко всем 5 зеркалам одновременно
        tasks = [client.head(f"{mirror}/{path}") for mirror in MIRROR_FILES]
        
        # Ждем первого, кто вернет 200 OK
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        
        for task in done:
            try:
                res = task.result()
                if res.status_code == 200:
                    for p in pending: p.cancel() # Останавливаем остальных
                    return str(res.url)
            except:
                continue
    
    # Если все упали или долго думали — отдаем Яндекс по умолчанию
    return f"{MIRROR_FILES[0]}/{path}"

@app.api_route("/packages/{path:path}", methods=["GET", "HEAD"])
async def proxy_packages(path: str):
    """Перехват скачивания файла: гонка и редирект."""
    fastest_url = await find_fastest_mirror(path)
    return RedirectResponse(url=fastest_url, status_code=302)
