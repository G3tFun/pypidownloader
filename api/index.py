import httpx
import re
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

app = FastAPI()

SOURCE_SIMPLE = "https://pypi.tuna.tsinghua.edu.cn/simple"

@app.get("/simple", response_class=HTMLResponse)
@app.get("/simple/", response_class=HTMLResponse)
async def get_full_index(request: Request):
    """Отдает полный список всех пакетов PyPI"""
    async with httpx.AsyncClient(timeout=60.0) as client:
        # Запрашиваем главный индекс у TUNA
        resp = await client.get(f"{SOURCE_SIMPLE}/", follow_redirects=True)
        
        # Чтобы ссылки внутри индекса (на конкретные пакеты) работали через нас,
        # нам нужно убедиться, что они относительные. 
        # TUNA обычно отдает их как <a href="package-name/">
        return HTMLResponse(content=resp.text)

@app.get("/simple/{package}/", response_class=HTMLResponse)
@app.get("/simple/{package}", response_class=HTMLResponse)
async def get_package_index(package: str, request: Request):
    """Отдает список версий конкретного пакета"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        target_url = f"{SOURCE_SIMPLE}/{package}/"
        resp = await client.get(target_url, follow_redirects=True)
        
        if resp.status_code != 200:
            return HTMLResponse("Package not found", status_code=404)

        # Главная магия: подменяем ссылки на файлы, чтобы они качались через Vercel
        # Мы меняем https://pypi.tuna.tsinghua.edu.cn/packages на /packages нашего домена
        base_url = str(request.base_url).rstrip('/')
        content = resp.text.replace(
            "https://pypi.tuna.tsinghua.edu.cn/packages", 
            f"{base_url}/packages"
        )
        return HTMLResponse(content=content)

@app.get("/packages/{path:path}")
async def stream_package_file(path: str):
    """Проксирует скачивание самих .whl и .tar.gz файлов"""
    file_url = f"https://pypi.tuna.tsinghua.edu.cn/packages/{path}"
    
    async def stream_file():
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("GET", file_url) as r:
                async for chunk in r.aiter_bytes(chunk_size=8192):
                    yield chunk

    return StreamingResponse(stream_file())
