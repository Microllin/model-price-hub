"""FastAPI 应用入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app import __version__
from app.api import prices, snapshots
from app.api.repository import data_date
from app.db.session import init_db

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

app.include_router(prices.router)
app.include_router(snapshots.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "version": __version__, "data_date": data_date()}


@app.get("/api", tags=["meta"])
def api_info():
    return {
        "name": "Model Price Hub",
        "version": __version__,
        "docs": "/docs",
        "endpoints": ["/v1/prices", "/v1/models", "/v1/providers", "/v1/snapshots"],
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
