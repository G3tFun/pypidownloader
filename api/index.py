from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
import httpx
import re

app = FastAPI()

# Источник данных - самое популярное зеркало
REMOTE_PYPI = "https://pypi.tuna.tsinghua.edu.cn/simple"
# Источник самих файлов (wheels)
REMOTE_FILES = "https://pypi.tuna.tsinghua.edu.cn/packages"

@app.get("/simple", response_class=HTMLResponse)
@app.get("/simple/", response_class=HTMLResponse)
async def list_packages():
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{REMOTE_PYPI}/")
        return res.text

@app.get("/simple/{package}", response_class=HTMLResponse)
async def get_package(package: str, request: Request):
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{REMOTE_PYPI}/{package}/")
        if res.status_code != 200:
            return HTMLResponse("Package not found", status_code=404)
        
        # Заменяем оригинальные ссылки на ссылки через наш Vercel
        content = res.text
        # Ищем ссылки на пакеты и перенаправляем их на наш эндпоинт /packages
        content = content.replace("https://pypi.tuna.tsinghua.edu.cn/packages", f"{request.base_url}packages")
        return content

@app.get("/packages/{path:path}")
async def get_file(path: str):
    # Стримим файл напрямую, чтобы не упереться в лимиты памяти Vercel
    file_url = f"{REMOTE_FILES}/{path}"
    
    async def stream_file():
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", file_url) as r:
                async for chunk in r.aiter_bytes():
                    yield chunk

    return StreamingResponse(stream_file())
