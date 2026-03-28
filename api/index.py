import asyncio
import httpx
import re
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse

app = FastAPI()

# Элитная пятерка зеркал для "гонки" за файлом (.whl)
MIRROR_FILES = [
    "https://mirror.yandex.ru/pypi/packages",
    "https://pypi.tuna.tsinghua.edu.cn/packages",
    "https://mirrors.aliyun.com/pypi/packages",
    "https://repo.huaweicloud.com/repository/pypi/packages",
    "https://files.pythonhosted.org/packages"  # Оригинал
]

# Список доменов, которые мы ищем в HTML и заменяем на свой /packages/
DOMAINS_TO_REPLACE = [
    r"https://files.pythonhosted.org/packages",
    r"https://mirror.yandex.ru/pypi/packages",
    r"https://pypi.tuna.tsinghua.edu.cn/packages",
    r"https://mirrors.aliyun.com/pypi/packages",
    r"https://repo.huaweicloud.com/repository/pypi/packages",
    r"https://[a-zA-Z0-9.-]+/pypi/packages",  # Универсальный паттерн
    r"https://[a-zA-Z0-9.-]+/packages"       # Еще один паттерн
]

@app.get("/")
async def root():
    return HTMLResponse("""
        <body style='font-family: sans-serif; text-align: center; padding-top: 50px;'>
            <h1>🌐 Smart PyPI Mirror 2.0</h1>
            <p>Race active: <b>Yandex, Google, Alibaba, Huawei, Tsinghua</b></p>
            <code>pip install [package] -i https://pypi-cdn.vercel.app/simple</code>
        </body>
    """)

@app.get("/simple/{package}")
@app.get("/simple/{package}/")
async def proxy_simple(package: str):
    """Получает список версий и подменяет все ссылки на локальные."""
    headers = {"User-Agent": "pip/24.0"}
    # Базовый источник для списка — Яндекс (самый быстрый пинг из РФ/СНГ)
    target_url = f"https://mirror.yandex.ru/pypi/simple/{package}/"
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
        try:
            resp = await client.get(target_url, headers=headers)
            if resp.status_code != 200:
                # Если Яндекс подвел, берем Туну (Китай)
                resp = await client.get(f"https://pypi.tuna.tsinghua.edu.cn/simple/{package}/", headers=headers)
            
            if resp.status_code == 200:
                content = resp.text
                # Заменяем все внешние домены на наш путь /packages/
                for domain in DOMAINS_TO_REPLACE:
                    content = re.sub(domain, "/packages", content)
                return HTMLResponse(content=content)
            
            return HTMLResponse(f"Package '{package}' not found", status_code=404)
        except Exception as e:
            return HTMLResponse(f"Server Error: {str(e)}", status_code=500)

async def find_fastest_mirror(path: str):
    """Гонка зеркал: опрашивает все сервера одновременно и выбирает первого."""
    async with httpx.AsyncClient(timeout=1.5) as client:
        # Создаем список задач на HEAD-запросы (только заголовки, это мгновенно)
        tasks = [client.head(f"{mirror}/{path}") for mirror in MIRROR_FILES]
        
        # Ждем первого, кто ответит статусом 200
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        
        for task in done:
            try:
                res = task.result()
                if res.status_code == 200:
                    # Отменяем остальные, чтобы не тратить лимиты Vercel
                    for p in pending: p.cancel()
                    return str(res.url)
            except:
                continue
    
    # Если никто не ответил вовремя, отдаем дефолт (Яндекс)
    return f"{MIRROR_FILES[0]}/{path}"

@app.get("/packages/{path:path}")
async def proxy_packages(path: str):
    """Перехватывает скачивание файла и делает редирект на самое быстрое зеркало."""
    fastest_url = await find_fastest_mirror(path)
    return RedirectResponse(url=fastest_url, status_code=302)
