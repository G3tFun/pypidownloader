import asyncio
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse

app = FastAPI()

# Список элитных зеркальных баз (пути к самим файлам)
MIRRORS = [
    "https://files.pythonhosted.org/packages",       # Оригинал (Fastly/Google)
    "https://pypi.tuna.tsinghua.edu.cn/packages",    # Китай (Золотой стандарт)
    "https://mirrors.aliyun.com/pypi/packages",      # Alibaba
    "https://mirror.yandex.ru/pypi/packages",        # Россия (Яндекс)
    "https://repo.huaweicloud.com/repository/pypi/packages", # Huawei
    "https://pypi.cloudflare.com/packages",          # Cloudflare
]

async def find_fastest_mirror(path: str):
    """Гонка за миллисекунды: опрашиваем мировые дата-центры одновременно."""
    # Таймаут 1.0 сек, чтобы не заставлять пользователя ждать
    async with httpx.AsyncClient(follow_redirects=True, timeout=1.0) as client:
        tasks = [client.head(f"{mirror}/{path}") for mirror in MIRRORS]
        
        # Ждем, пока кто-то один не скажет "Я здесь и я быстрый!"
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        
        for task in done:
            try:
                response = task.result()
                if response.status_code == 200:
                    # Убиваем остальные запросы, победителю — всё!
                    for p in pending: p.cancel()
                    return str(response.url)
            except Exception:
                continue
        
        # Если все промолчали (бывает на редких пакетах), идем на оригинал
        return f"{MIRRORS[0]}/{path}"

@app.get("/")
async def root():
    return HTMLResponse("""
        <body style='font-family: sans-serif; text-align: center; padding-top: 50px;'>
            <h1>🌐 Smart PyPI Mirror is Live</h1>
            <p>Race active between: <b>Yandex, Google, Alibaba, Huawei, Cloudflare</b></p>
            <code>pip install package -i https://pypi-cdn.vercel.app/simple</code>
        </body>
    """)

@app.get("/simple/{package}/")
@app.get("/simple/{package}")
async def proxy_simple(package: str):
    """Подменяем ссылки в индексе на свои, чтобы перехватить скачивание."""
    # Используем Яндекс для получения списка версий (он очень быстрый в РФ)
    target_url = f"https://mirror.yandex.ru/pypi/simple/{package}/"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(target_url)
        # Массовая замена всех возможных путей на наш локальный /packages/
        content = resp.text
        for m in ["https://files.pythonhosted.org/packages", 
                  "https://pypi.tuna.tsinghua.edu.cn/packages",
                  "https://mirror.yandex.ru/pypi/packages"]:
            content = content.replace(m, "/packages")
        return HTMLResponse(content=content)

@app.get("/packages/{path:path}")
async def proxy_packages(path: str):
    """Главный чит: мгновенный редирект на лучшего из списка."""
    fastest_url = await find_fastest_mirror(path)
    return RedirectResponse(url=fastest_url, status_code=302)
