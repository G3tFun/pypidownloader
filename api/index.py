import asyncio
import re
import time
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

# --------------------------------------------------------------------------
# Конфигурация
# --------------------------------------------------------------------------

MIRROR_FILES = [
    "https://mirror.yandex.ru/pypi/packages",
    "https://pypi.tuna.tsinghua.edu.cn/packages",
    "https://mirrors.aliyun.com/pypi/packages",
    "https://repo.huaweicloud.com/repository/pypi/packages",
    "https://mirrors.cloud.tencent.com/pypi/packages",
    "https://mirrors.ustc.edu.cn/pypi/web/packages",
    "https://mirror.sjtu.edu.cn/pypi/web/packages",
    "https://mirrors.bfsu.edu.cn/pypi/web/packages",
    "https://mirrors.pku.edu.cn/pypi/web/packages",
    "https://mirrors.nju.edu.cn/pypi/web/packages",
    "https://pypi.mirrors.ustc.edu.cn/packages",
    "https://files.pythonhosted.org/packages",
]

INDEX_MIRRORS = [
    "https://pypi.org/simple/{pkg}/",
    "https://pypi.tuna.tsinghua.edu.cn/simple/{pkg}/",
    "https://mirror.yandex.ru/pypi/simple/{pkg}/",
    "https://mirrors.aliyun.com/pypi/simple/{pkg}/",
    "https://repo.huaweicloud.com/repository/pypi/simple/{pkg}/",
    "https://mirrors.cloud.tencent.com/pypi/simple/{pkg}/",
    "https://mirrors.ustc.edu.cn/pypi/web/simple/{pkg}/",
    "https://mirror.sjtu.edu.cn/pypi/web/simple/{pkg}/",
    "https://mirrors.bfsu.edu.cn/pypi/web/simple/{pkg}/",
    "https://mirrors.pku.edu.cn/pypi/web/simple/{pkg}/",
    "https://mirrors.nju.edu.cn/pypi/web/simple/{pkg}/",
]

DOMAINS_TO_REPLACE = re.compile(
    r"https?://[a-zA-Z0-9.-]+(?:/[a-zA-Z0-9.-]+)*/packages"
)

HTTP_TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0)
RACE_TIMEOUT = 3.5          # сколько ждём победителя гонки зеркал
INDEX_CACHE_TTL = 60        # сек — кэш simple-индекса пакета
MIRROR_CACHE_TTL = 300      # сек — кэш "какое зеркало быстрее отдало файл"

# --------------------------------------------------------------------------
# Общий на весь процесс httpx.AsyncClient (переиспользует соединения,
# живёт, пока жив тёплый serverless-инстанс)
# --------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None

# Простые in-memory кэши. На serverless это работает только в рамках
# "тёплого" инстанса, но это всё равно ощутимо снижает latency при
# повторных запросах (pip обычно ходит несколько раз подряд).
_index_cache: dict[str, tuple[float, bytes, str]] = {}
_mirror_cache: dict[str, tuple[float, str]] = {}

# Статистика побед по доменам (живёт, пока жив тёплый serverless-инстанс).
# domain -> число побед в гонке find_fastest_mirror
_domain_wins: dict[str, int] = {}
_last_winner: dict[str, str | None] = {"domain": None, "url": None, "path": None, "time": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client
    _client = httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True)
    try:
        yield
    finally:
        await _client.aclose()


app = FastAPI(title="Smart PyPI Proxy", lifespan=lifespan)


def get_client() -> httpx.AsyncClient:
    # На случай холодного старта без lifespan (некоторые окружения его
    # не вызывают в точности так, как ожидает ASGI)
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True)
    return _client


def rewrite_links(content: str) -> str:
    content = content.replace("../../packages", "/packages")
    content = content.replace("../packages", "/packages")
    return DOMAINS_TO_REPLACE.sub("/packages", content)


def safe_join_path(path: str) -> str | None:
    """Не даём path traversal вылезти за пределы /packages/."""
    if ".." in path.split("/") or path.startswith("/"):
        return None
    return path


# --------------------------------------------------------------------------
# Роуты
# --------------------------------------------------------------------------

@app.get("/")
async def root():
    return HTMLResponse(
        "<h1>🌐 Smart PyPI Proxy is Live</h1>"
        "<p>Use: <code>pip install &lt;package&gt; -i https://your-domain/simple</code></p>"
    )


@app.get("/health")
async def health():
    return {"status": "ok", "time": time.time()}


@app.api_route("/simple/{package}/", methods=["GET", "HEAD"])
@app.api_route("/simple/{package}", methods=["GET", "HEAD"])
async def proxy_simple(package: str, request: Request):
    """Отдаёт simple-индекс пакета, с fallback по зеркалам и кэшем."""

    safe_package = package.replace("_", "-").lower()
    client_accept = request.headers.get("Accept", "text/html")
    headers = {"User-Agent": "pip/24.0", "Accept": client_accept}

    cache_key = f"{safe_package}:{client_accept}"
    now = time.time()

    cached = _index_cache.get(cache_key)
    if cached and now - cached[0] < INDEX_CACHE_TTL:
        _, body, content_type = cached
        if request.method == "HEAD":
            return Response(status_code=200, media_type=content_type)
        return Response(content=body, media_type=content_type,
                         headers={"Cache-Control": f"public, max-age={INDEX_CACHE_TTL}"})

    client = get_client()
    candidates = [safe_package]
    if safe_package != package:
        candidates.append(package)  # редкий fallback на исходное имя

    for pkg in candidates:
        for template in INDEX_MIRRORS:
            url = template.format(pkg=pkg)
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    content = rewrite_links(resp.text)
                    content_type = resp.headers.get("Content-Type", "text/html")
                    _index_cache[cache_key] = (now, content.encode(), content_type)

                    if request.method == "HEAD":
                        return Response(status_code=200, media_type=content_type)
                    return Response(
                        content=content,
                        media_type=content_type,
                        headers={"Cache-Control": f"public, max-age={INDEX_CACHE_TTL}"},
                    )
            except Exception:
                continue

    return HTMLResponse(f"Package {package} not found on any mirror", status_code=404)


async def find_fastest_mirror(path: str) -> str:
    """Гонка за файлом: первый живой 200 OK побеждает,
    остальные незавершённые запросы отменяются сразу же."""

    cached = _mirror_cache.get(path)
    if cached and time.time() - cached[0] < MIRROR_CACHE_TTL:
        return cached[1]

    client = get_client()
    tasks = {
        asyncio.create_task(client.head(f"{mirror}/{path}")): mirror
        for mirror in MIRROR_FILES
    }

    winner_url = None
    try:
        pending = set(tasks.keys())
        deadline = asyncio.get_event_loop().time() + RACE_TIMEOUT
        while pending:
            timeout = deadline - asyncio.get_event_loop().time()
            if timeout <= 0:
                break
            done, pending = await asyncio.wait(
                pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                try:
                    res = task.result()
                    if res.status_code == 200:
                        winner_url = str(res.url)
                        break
                except Exception:
                    continue
            if winner_url:
                break
    finally:
        # Обязательно гасим все незавершённые задачи — иначе на
        # serverless они останутся висеть до заморозки инстанса.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.keys(), return_exceptions=True)

    result = winner_url or f"https://files.pythonhosted.org/packages/{path}"
    _mirror_cache[path] = (time.time(), result)

    # Фиксируем победителя для статистики /api/top/service
    domain = urlparse(result).netloc
    _domain_wins[domain] = _domain_wins.get(domain, 0) + 1
    _last_winner.update(domain=domain, url=result, path=path, time=time.time())

    return result


@app.get("/api/top/service")
async def top_service():
    """Показывает домен-победитель последней гонки и общий рейтинг зеркал.

    Статистика накапливается только в рамках текущего тёплого
    serverless-инстанса (in-memory), после холодного старта обнуляется.
    """
    if not _domain_wins:
        return {
            "top_domain": None,
            "message": "Гонок ещё не было — статистика появится после первого запроса к /packages/...",
            "last_winner": None,
            "ranking": [],
        }

    ranking = sorted(_domain_wins.items(), key=lambda kv: kv[1], reverse=True)
    top_domain, top_wins = ranking[0]

    return {
        "top_domain": top_domain,
        "top_wins": top_wins,
        "last_winner": _last_winner,
        "ranking": [{"domain": d, "wins": w} for d, w in ranking],
    }


@app.api_route("/packages/{path:path}", methods=["GET", "HEAD"])
async def proxy_packages(path: str):
    """Перехватываем запрос на файл и редиректим на лучшее зеркало."""
    clean_path = safe_join_path(path)
    if clean_path is None:
        return Response(status_code=400, content="Invalid path")

    fastest_url = await find_fastest_mirror(clean_path)
    return RedirectResponse(url=fastest_url, status_code=302)
