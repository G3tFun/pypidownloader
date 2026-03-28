from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
import httpx

app = FastAPI()

# Зеркало-источник (TUNA)
SOURCE_SIMPLE = "https://pypi.tuna.tsinghua.edu.cn/simple"
SOURCE_PACKAGES = "https://pypi.tuna.tsinghua.edu.cn/packages"

@app.get("/simple/{package}")
@app.get("/simple/{package}/")
async def proxy_simple(package: str, request: Request):
    async with httpx.AsyncClient() as client:
        # 1. Запрашиваем страницу пакета у китайцев
        target_url = f"{SOURCE_SIMPLE}/{package}/"
        resp = await client.get(target_url, follow_redirects=True)
        
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code)

        # 2. Подменяем их ссылки на наши, чтобы pip качал через твой Vercel
        # Мы меняем оригинальный домен на домен твоего сайта в Vercel
        base_url = str(request.base_url).rstrip('/')
        content = resp.text.replace(
            "https://pypi.tuna.tsinghua.edu.cn/packages", 
            f"{base_url}/packages"
        )
        return HTMLResponse(content=content)

@app.get("/packages/{path:path}")
async def proxy_packages(path: str):
    # 3. Стримим сам файл (wheel/tar.gz) напрямую
    file_url = f"{SOURCE_PACKAGES}/{path}"
    
    async def stream_file():
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("GET", file_url) as r:
                async for chunk in r.aiter_bytes():
                    yield chunk

    return StreamingResponse(stream_file())

@app.get("/")
async def root():
    return {"status": "PyPI Mirror is running", "source": "Tsinghua TUNA"}
