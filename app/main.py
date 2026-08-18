"""FastAPI 应用入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app import __version__
from app.api import prices, snapshots, webhooks, discovery, sources, catalog, admin
from app.api.repository import data_date
from app.db.session import init_db
from app.pipeline.health import read_status
from datetime import datetime, timezone

WEB_DIR = Path(__file__).parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Model Price Hub",
    version=__version__,
    description="每日聚合主流大模型价格(国内 CNY / 国外 USD)",
    lifespan=lifespan,
)

app.include_router(admin.router)
app.include_router(prices.router)
app.include_router(snapshots.router)
app.include_router(webhooks.router)
app.include_router(discovery.router)
app.include_router(sources.router)
app.include_router(catalog.router)


@app.get("/health", tags=["meta"])
def health():
    """服务与抓取管线健康状态。API 本身正常不代表数据更新正常。"""
    pipeline = read_status()
    status = pipeline.get("status", "unknown")
    if status == "running":
        try:
            started = datetime.fromisoformat(pipeline["started_at"])
            if (datetime.now(timezone.utc) - started).total_seconds() > 2 * 3600:
                status = "stale"
        except (KeyError, ValueError, TypeError):
            status = "stale"
    return {
        "status": "ok" if status in {"success", "unknown"} else "degraded",
        "version": __version__,
        "data_date": data_date(),
        "pipeline": {**pipeline, "status": status},
    }


@app.get("/api", tags=["meta"])
def api_info():
    return {
        "name": "Model Price Hub",
        "version": __version__,
        "docs": "/docs",
        "endpoints": ["/v1/prices", "/v1/models", "/v1/providers", "/v1/snapshots", "/v1/webhooks", "/v1/discovery", "/v1/sources", "/v1/catalog", "/v1/admin"],
    }


@app.get("/", include_in_schema=False)
def web_ui():
    """价格浏览页(单页 Web UI)。"""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/favicon.svg", include_in_schema=False)
def favicon_svg():
    """站点图标(矢量,现代浏览器首选)。"""
    return FileResponse(WEB_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico():
    """兼容旧浏览器/默认约定的 /favicon.ico 请求,复用同一 SVG。"""
    return FileResponse(WEB_DIR / "favicon.svg", media_type="image/svg+xml")
