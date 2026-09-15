"""官方模型定价中心 — 前台浏览 + 后台管理。

启动:
    cd model-price-hub
    python -m app.serve_official          # 默认 0.0.0.0:8081

数据范围(前台):
    channel='official' AND source!='litellm' AND provider.type='VENDOR'
    —— 只展示自家爬虫从厂商官网爬取的定价,不含聚合平台数据。

前台:
    GET /                       → 价格浏览页(黑夜/白天、中英文)
    GET /api/overview           → 统计概览
    GET /api/vendors            → 厂商列表
    GET /api/prices             → 价格表
    GET /api/compare            → 同模型比价

后台(/admin,需登录):
    脚本爬取:
        GET  /api/admin/scrapers                 脚本列表+状态
        POST /api/admin/scrapers/{name}/run      手动运行单个
        POST /api/admin/scrapers/run-all         全局更新
        GET  /api/admin/scrapers/{name}/detail   错误详情/修复建议
        POST /api/admin/scrapers/{name}/ai-repair 一键 AI 修复
        PUT  /api/admin/vendors/{slug}           厂商信息维护(中文名/域名)
    API Key:
        GET/POST/PUT/DELETE /api/admin/llm-keys  LLM Key 管理
        POST /api/admin/llm-keys/{kid}/test      连通性测试
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import traceback
from urllib.parse import urlparse
import py_compile
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, distinct
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.schema_v2 import (
    Provider, Model, PricePlan, ProviderType,
)

# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------

DB_PATH = Path("data/v2.sqlite")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

DATA = settings.data_dir
SCRAPER_STATUS_PATH = DATA / "scraper-status.json"
LLM_KEYS_PATH = DATA / "llm-keys.json"
VENDOR_INFO_PATH = DATA / "vendor-info.json"
REPAIR_TASKS_PATH = DATA / "repair-tasks.json"
VISION_OVERRIDES_PATH = DATA / "vision-overrides.json"
SCRAPER_DRAFTS_PATH = DATA / "scraper-drafts.json"
SESSION_COOKIE = "mph_official_session"
SESSION_TTL = 7 * 24 * 3600

WEB_DIR = Path(__file__).resolve().parent / "web" / "official"
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

app = FastAPI(title="官方模型定价中心")
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 厂商中文名映射(可被 vendor-info.json 覆盖)
# ---------------------------------------------------------------------------

VENDOR_ZH: dict[str, dict[str, str]] = {
    "aliyun":     {"zh": "阿里云·通义千问", "domain": "help.aliyun.com"},
    "bytedance":  {"zh": "字节跳动·豆包", "domain": "volcengine.com"},
    "google":     {"zh": "谷歌 Gemini", "domain": "ai.google.dev"},
    "zhipu":      {"zh": "智谱 GLM", "domain": "bigmodel.cn"},
    "anthropic":  {"zh": "Anthropic Claude", "domain": "docs.anthropic.com"},
    "openai":     {"zh": "OpenAI", "domain": "developers.openai.com"},
    "minimax":    {"zh": "MiniMax 海螺", "domain": "minimaxi.com"},
    "moonshot":   {"zh": "月之暗面 Kimi", "domain": "platform.moonshot.cn"},
    "baidu":      {"zh": "百度文心", "domain": "cloud.baidu.com"},
    "deepseek":   {"zh": "DeepSeek 深度求索", "domain": "api-docs.deepseek.com"},
    "iflytek":    {"zh": "科大讯飞·星火", "domain": "xfyun.cn"},
    "tencent":    {"zh": "腾讯混元", "domain": "cloud.tencent.com"},
    "01ai":       {"zh": "零一万物 Yi", "domain": "platform.lingyiwanwu.com"},
    "xai":        {"zh": "xAI Grok", "domain": "docs.x.ai"},
    "mistral":    {"zh": "Mistral AI", "domain": "docs.mistral.ai"},
    "cohere":     {"zh": "Cohere", "domain": "docs.cohere.com"},
    "perplexity": {"zh": "Perplexity", "domain": "docs.perplexity.ai"},
    "ai21":       {"zh": "AI21 Labs", "domain": "docs.ai21.com"},
    "meta":       {"zh": "Meta Llama", "domain": "llama.meta.com"},
    "amazon":     {"zh": "亚马逊 Nova", "domain": "aws.amazon.com"},
    "stepfun":    {"zh": "阶跃星辰", "domain": "platform.stepfun.com"},
}


def vendor_info(slug: str, display_name: str) -> dict[str, str]:
    """合并内置中文名与 vendor-info.json 里的手工维护值。"""
    overrides = _read_json(VENDOR_INFO_PATH, {})
    base = VENDOR_ZH.get(slug, {"zh": display_name, "domain": ""})
    ov = overrides.get(slug, {})
    return {
        "zh": ov.get("zh") or base["zh"],
        "domain": ov.get("domain") or base.get("domain", ""),
    }


# ---------------------------------------------------------------------------
# 官方数据过滤
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 价格新鲜度判定
# ---------------------------------------------------------------------------

# 同一轮抓取内各行的 scraped_at 天然相差数秒，比较时必须留容差。
# 实测不留容差会把在展的 550 行里误标 533 行为陈旧，功能等于废掉。
# 容差取 1 小时~7 天结果完全一致(均为 77 行)，说明真实边界很清晰，取 1 小时。
FRESHNESS_TOLERANCE = timedelta(hours=1)

# 这些状态不代表抓取失败:unavailable 是配置性跳过(如未配视觉凭据)，
# running/never 是尚无结论。把它们当故障会变成谎报军情。
_NON_FAILURE_STATUS = {"unavailable", "running", "never"}

# _normalize_probe_status() 里对这条旧错误信息的降级处理，在前台同样适用；
# 但前台不实例化 scraper registry(太重)，所以只搬这一条基于文本的规则。
_EMPTY_RESULT_ERROR = "抓取成功但返回 0 条数据,官方页面结构可能已变更"


def _as_utc(value: Any) -> datetime | None:
    """统一成带时区的 UTC。price_plans.scraped_at 是 naive UTC，
    而 scraper-status.json 里的时间戳带 +00:00，不统一就没法比较。"""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _iso_utc(value: Any) -> str | None:
    """序列化成带时区的 ISO 串。不带时区的话 JS new Date() 会按本地时区
    解析 naive UTC，CST 下整整差 8 小时。"""
    parsed = _as_utc(value)
    return parsed.isoformat() if parsed else None


def _source_failed(status: dict[str, Any] | None) -> bool:
    """该源最近一次运行是否失败(跑过，但没跑成功)。"""
    if not status:
        return False  # 无状态记录(如 override 人工覆盖数据)不算故障
    if status.get("status") in _NON_FAILURE_STATUS:
        return False
    if status.get("error") == _EMPTY_RESULT_ERROR:
        return False  # 与 _normalize_probe_status 保持一致:降级为待复核，不算故障
    last_run = _as_utc(status.get("last_run"))
    if last_run is None:
        return False
    last_success = _as_utc(status.get("last_success"))
    return last_success is None or last_run > last_success


def classify_freshness(
    *,
    scraped_at: Any,
    is_current: bool,
    source: str,
    status_store: dict[str, Any],
    source_latest: dict[str, Any],
) -> tuple[str, str | None]:
    """判断单条价格的新鲜度，返回 (freshness, stale_reason)。

    按顺序短路:
      1. 已被软过期        → delisted   (厂商下架，不是爬虫故障)
      2. 该源最近一次跑失败 → stale/source_failed
      3. 同源其他行刷新了、本行没有 → stale/not_refreshed
      4. 其余              → fresh
    """
    if not is_current:
        return "delisted", None
    status = (status_store or {}).get(source) or {}
    if _source_failed(status):
        return "stale", "source_failed"

    # 基准时间取「同源在展行的最新入库时刻」与「该源最近一次成功时刻」的较大值。
    # 只用前者的话,某个源整体一条都没刷新时(所有行都旧)基准会退化成行自己的
    # 时间,永远判不出陈旧;后者能戳穿这种情况。
    current = _as_utc(scraped_at)
    candidates = [
        ts for ts in (
            _as_utc((source_latest or {}).get(source)),
            _as_utc(status.get("last_success")),
        ) if ts
    ]
    if current and candidates and current < max(candidates) - FRESHNESS_TOLERANCE:
        return "stale", "not_refreshed"
    return "fresh", None


def official_filters():
    return [
        PricePlan.channel == "official",
        PricePlan.source != "litellm",
        Provider.type == ProviderType.VENDOR,
    ]


def _source_latest_scraped(session: Any) -> dict[str, Any]:
    """每个数据源在展行里最近一次刷新出数据的时间。
    用于判断"同源其他行更新了、本行没有"。584 行量级，开销可忽略。"""
    rows = (
        session.query(PricePlan.source, func.max(PricePlan.scraped_at))
        .select_from(PricePlan)
        .join(Model, PricePlan.model_id == Model.id)
        .join(Provider, Model.provider_id == Provider.id)
        .filter(*official_filters())
        .filter(PricePlan.is_current.is_(True))
        .group_by(PricePlan.source)
        .all()
    )
    return {source: latest for source, latest in rows}


# ---------------------------------------------------------------------------
# 前台 API
# ---------------------------------------------------------------------------

@app.get("/api/overview")
def overview():
    with SessionLocal() as s:
        base = (
            s.query(PricePlan)
            .join(Model, PricePlan.model_id == Model.id)
            .join(Provider, Model.provider_id == Provider.id)
            .filter(*official_filters())
        )
        total_prices = base.count()
        total_models = (
            s.query(func.count(distinct(Model.id)))
            .select_from(Model)
            .join(PricePlan, PricePlan.model_id == Model.id)
            .join(Provider, Model.provider_id == Provider.id)
            .filter(*official_filters())
            .scalar()
        )
        total_vendors = (
            s.query(func.count(distinct(Provider.id)))
            .select_from(Provider)
            .join(Model, Model.provider_id == Provider.id)
            .join(PricePlan, PricePlan.model_id == Model.id)
            .filter(*official_filters())
            .scalar()
        )
        sources = [
            r[0] for r in
            s.query(distinct(PricePlan.source))
            .select_from(PricePlan)
            .join(Model, PricePlan.model_id == Model.id)
            .join(Provider, Model.provider_id == Provider.id)
            .filter(*official_filters())
            .all()
        ]
        modalities = [
            r[0] for r in
            s.query(distinct(Model.modality))
            .select_from(Model)
            .join(PricePlan, PricePlan.model_id == Model.id)
            .join(Provider, Model.provider_id == Provider.id)
            .filter(*official_filters())
            .all()
        ]
        return {
            "vendors": total_vendors,
            "models": total_models,
            "prices": total_prices,
            "sources": sorted(sources),
            "modalities": sorted(m for m in modalities if m),
        }


@app.get("/api/vendors")
def list_vendors():
    with SessionLocal() as s:
        rows = (
            s.query(
                Provider.slug,
                Provider.display_name,
                Provider.pricing_url,
                func.count(distinct(Model.id)).label("model_count"),
                func.count(PricePlan.id).label("price_count"),
            )
            .select_from(Provider)
            .join(Model, Model.provider_id == Provider.id)
            .join(PricePlan, PricePlan.model_id == Model.id)
            .filter(*official_filters())
            .group_by(Provider.id)
            .order_by(func.count(PricePlan.id).desc())
            .all()
        )
        out = []
        for r in rows:
            vi = vendor_info(r.slug, r.display_name)
            out.append({
                "slug": r.slug,
                "name": r.display_name,
                "zh": vi["zh"],
                "domain": vi["domain"],
                "pricing_url": r.pricing_url,
                "models": r.model_count,
                "prices": r.price_count,
            })
        return out


@app.get("/api/prices")
def list_prices(
    vendor: str | None = None,
    modality: str | None = None,
    currency: str | None = None,
    billing_unit: str | None = None,
    q: str | None = None,
    sort: str = "vendor",
    limit: int = 500,
    offset: int = 0,
):
    with SessionLocal() as s:
        query = (
            s.query(
                Provider.slug.label("vendor"),
                Provider.display_name.label("vendor_name"),
                Model.model_id,
                Model.display_name.label("model_name"),
                Model.canonical,
                Model.modality,
                Model.context_window,
                Model.max_output,
                PricePlan.currency,
                PricePlan.billing_unit,
                PricePlan.service_tier,
                PricePlan.cache_state,
                PricePlan.context_range,
                PricePlan.deployment_version,
                PricePlan.input_price,
                PricePlan.output_price,
                PricePlan.cached_read,
                PricePlan.cached_write,
                PricePlan.source,
                PricePlan.source_url,
                PricePlan.scraped_at,
                PricePlan.time_window,
                PricePlan.is_current,
                PricePlan.effective_to,
            )
            .select_from(PricePlan)
            .join(Model, PricePlan.model_id == Model.id)
            .join(Provider, Model.provider_id == Provider.id)
            .filter(*official_filters())
        )
        if vendor:
            query = query.filter(Provider.slug == vendor)
        if modality:
            query = query.filter(Model.modality == modality)
        if currency:
            query = query.filter(PricePlan.currency == currency)
        if billing_unit:
            query = query.filter(PricePlan.billing_unit == billing_unit)
        if q:
            query = query.filter(Model.model_id.ilike(f"%{q}%"))

        total = query.count()
        order_map = {
            "vendor": [Provider.slug, Model.model_id],
            "model": [Model.model_id, Provider.slug],
            "input": [PricePlan.input_price, Provider.slug],
            "output": [PricePlan.output_price, Provider.slug],
        }
        for col in order_map.get(sort, order_map["vendor"]):
            query = query.order_by(col)
        rows = query.offset(offset).limit(limit).all()

        status_store = _status_store()
        source_latest = _source_latest_scraped(s)

        items = []
        for r in rows:
            vi = vendor_info(r.vendor, r.vendor_name)
            freshness, stale_reason = classify_freshness(
                scraped_at=r.scraped_at,
                is_current=bool(r.is_current),
                source=r.source,
                status_store=status_store,
                source_latest=source_latest,
            )
            src_status = status_store.get(r.source) or {}
            items.append({
                "vendor": r.vendor,
                "vendor_name": r.vendor_name,
                "vendor_zh": vi["zh"],
                "model": r.model_id,
                "model_name": r.model_name,
                "canonical": r.canonical,
                "modality": r.modality,
                "context_window": r.context_window,
                "max_output": r.max_output,
                "currency": r.currency.value if hasattr(r.currency, "value") else r.currency,
                "billing_unit": r.billing_unit.value if hasattr(r.billing_unit, "value") else r.billing_unit,
                "service_tier": r.service_tier,
                "cache_state": r.cache_state,
                "context_range": r.context_range,
                "deployment_version": r.deployment_version,
                "input_price": float(r.input_price) if r.input_price is not None else None,
                "output_price": float(r.output_price) if r.output_price is not None else None,
                "cached_read": float(r.cached_read) if r.cached_read is not None else None,
                "cached_write": float(r.cached_write) if r.cached_write is not None else None,
                "source": r.source,
                "source_url": r.source_url,
                "scraped_at": _iso_utc(r.scraped_at),
                "time_window": r.time_window,
                "freshness": freshness,
                "stale_reason": stale_reason,
                "source_last_success": _iso_utc(src_status.get("last_success")),
            })
        return {"total": total, "items": items}


@app.get("/api/compare")
def compare(canonical: str):
    with SessionLocal() as s:
        rows = (
            s.query(
                Provider.slug, Provider.display_name,
                Model.model_id, Model.context_window,
                PricePlan.currency, PricePlan.billing_unit,
                PricePlan.input_price, PricePlan.output_price,
                PricePlan.cached_read, PricePlan.cached_write,
                PricePlan.service_tier, PricePlan.source,
            )
            .select_from(PricePlan)
            .join(Model, PricePlan.model_id == Model.id)
            .join(Provider, Model.provider_id == Provider.id)
            .filter(*official_filters())
            .filter(Model.canonical.ilike(f"%{canonical}%"))
            .order_by(PricePlan.input_price)
            .all()
        )
        return [
            {
                "vendor": r[0], "vendor_name": r[1], "model": r[2],
                "context_window": r[3],
                "currency": r[4].value if hasattr(r[4], "value") else r[4],
                "billing_unit": r[5].value if hasattr(r[5], "value") else r[5],
                "input_price": float(r[6]) if r[6] is not None else None,
                "output_price": float(r[7]) if r[7] is not None else None,
                "cached_read": float(r[8]) if r[8] is not None else None,
                "cached_write": float(r[9]) if r[9] is not None else None,
                "service_tier": r[10], "source": r[11],
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# 后台认证(复用 data/admin.json 的凭据)
# ---------------------------------------------------------------------------

def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 240_000).hex()


def _admin() -> dict[str, Any]:
    value = _read_json(DATA / "admin.json", {})
    if value:
        return value
    if not settings.admin_password:
        return {}
    salt = secrets.token_hex(16)
    value = {
        "username": settings.admin_username, "salt": salt,
        "password_hash": _hash_password(settings.admin_password, salt),
        "created_at": _now(),
    }
    _write_json(DATA / "admin.json", value)
    return value


def _sessions() -> dict[str, Any]:
    return _read_json(DATA / "admin-sessions.json", {})


def require_admin(request: Request) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE, "")
    session = _sessions().get(token)
    if not session or session.get("expires_at", 0) < time.time():
        raise HTTPException(401, "请先登录后台")
    return session


class LoginInput(BaseModel):
    username: str
    password: str


@app.post("/api/admin/login")
def admin_login(body: LoginInput, response: Response):
    admin = _admin()
    if not admin:
        raise HTTPException(503, "尚未配置管理员,请设置 MPH_ADMIN_USERNAME / MPH_ADMIN_PASSWORD")
    expected = _hash_password(body.password, admin["salt"])
    if not (hmac.compare_digest(body.username, admin["username"])
            and hmac.compare_digest(expected, admin["password_hash"])):
        raise HTTPException(401, "用户名或密码错误")
    token = secrets.token_urlsafe(32)
    sessions = {k: v for k, v in _sessions().items() if v.get("expires_at", 0) > time.time()}
    sessions[token] = {"username": admin["username"], "created_at": time.time(),
                       "expires_at": time.time() + SESSION_TTL}
    _write_json(DATA / "admin-sessions.json", sessions)
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax")
    return {"ok": True, "username": admin["username"]}


@app.post("/api/admin/logout")
def admin_logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE, "")
    sessions = _sessions()
    sessions.pop(token, None)
    _write_json(DATA / "admin-sessions.json", sessions)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/admin/me")
def admin_me(admin: dict = Depends(require_admin)):
    return {"username": admin["username"], "expires_at": admin["expires_at"]}


# ---------------------------------------------------------------------------
# 后台 · 脚本爬取管理
# ---------------------------------------------------------------------------

# 非官方渠道源(聚合/转售),不进「脚本爬取」管理
NON_OFFICIAL_SOURCES = {"litellm", "openrouter", "siliconflow", "ppio"}


def _official_scrapers() -> list[Any]:
    """返回注册表中的官方抓取器实例。"""
    from app.scrapers.registry import all_scrapers
    return [s for s in all_scrapers() if s.source_name not in NON_OFFICIAL_SOURCES]


def _scraper_source_path(name: str) -> Path:
    """Resolve a registered scraper to a file inside app/scrapers only."""
    scraper = next((s for s in _official_scrapers() if s.source_name == name), None)
    if scraper is None:
        raise HTTPException(404, f"未找到采集器: {name}")
    import inspect
    path = Path(inspect.getfile(scraper.__class__)).resolve()
    allowed = (Path(__file__).resolve().parent / "scrapers").resolve()
    if allowed not in path.parents or path.suffix != ".py":
        raise HTTPException(400, "采集器源码路径不在允许目录内")
    return path


def _scraper_registry() -> list[dict[str, Any]]:
    """从 registry 拿到官方渠道抓取器清单(延迟导入,避免拖慢前台启动)。"""
    out = []
    for s in _official_scrapers():
        out.append({
            "name": s.source_name,
            "class": s.__class__.__name__,
            "provider": getattr(s, "provider", ""),
            "source_url": getattr(s, "source_url", ""),
            "requires_render": getattr(s, "requires_render", False),
            "fetch_chain": getattr(s, "_effective_chain", []),
            "collection_mode": getattr(s, "collection_mode", "http"),
            "needs_playwright": getattr(s, "needs_playwright", False),
            "needs_vision_credentials": getattr(s, "needs_vision_credentials", False),
            "is_vision": s.source_name.startswith("vision-") or "vision" in s.__class__.__name__.lower(),
        })
    return out


def _status_store() -> dict[str, Any]:
    return _read_json(SCRAPER_STATUS_PATH, {})


def _provider_baselines() -> dict[str, dict[str, Any]]:
    """返回已入库的官方脚本数据，和即时探测状态分开呈现。"""
    with SessionLocal() as s:
        rows = (
            s.query(
                Provider.slug,
                func.count(PricePlan.id),
                func.max(PricePlan.scraped_at),
            )
            .select_from(Provider)
            .join(Model, Model.provider_id == Provider.id)
            .join(PricePlan, PricePlan.model_id == Model.id)
            .filter(*official_filters())
            .group_by(Provider.slug)
            .all()
        )
    return {
        slug: {
            "rows": count,
            "last_scraped": last.isoformat() if last else None,
        }
        for slug, count, last in rows
    }


@lru_cache(maxsize=1)
def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


def _vision_credentials_available() -> bool:
    try:
        from app.scrapers.vision_base import _has_credentials
        return _has_credentials()
    except Exception:
        return False


def _scraper_unavailable_reason(scraper: Any) -> str | None:
    """识别配置性跳过；这种情况不代表脚本损坏。"""
    from app.scrapers.base import BaseScraper

    is_vision = (
        scraper.source_name.startswith("vision-")
        or "vision" in scraper.__class__.__name__.lower()
    )
    if is_vision:
        if not settings.use_playwright:
            return "当前服务未启用 Playwright，视觉验证未执行"
        from app.scrapers.vision_base import _has_credentials
        if not _has_credentials():
            return "未配置视觉 OCR 凭据，视觉验证未执行"

    if getattr(scraper, "needs_playwright", False) and not settings.use_playwright:
        return "该采集器必须使用 Playwright，当前配置未启用浏览器"
    return None


def _normalize_probe_status(scraper: Any, raw: dict[str, Any]) -> dict[str, Any]:
    """兼容旧监控记录，并避免把配置性跳过误报成脚本故障。"""
    status = dict(raw)
    unavailable_reason = _scraper_unavailable_reason(scraper)
    if unavailable_reason:
        status.update({"status": "unavailable", "error": None, "message": unavailable_reason})
    elif status.get("error") == "抓取成功但返回 0 条数据,官方页面结构可能已变更":
        status.update({
            "status": "warning",
            "error": None,
            "message": "本次探测未解析出数据，历史入库数据不受影响；需复核后才能判定页面结构变化",
        })
    return status


def _save_status(name: str, patch: dict[str, Any]) -> None:
    store = _status_store()
    cur = store.get(name, {})
    cur.update(patch)
    cur["updated_at"] = _now()
    store[name] = cur
    _write_json(SCRAPER_STATUS_PATH, store)


@app.get("/api/admin/scrapers")
def admin_scrapers(admin: dict = Depends(require_admin)):
    """脚本列表 + 运行状态 + 最近错误。"""
    store = _status_store()
    repairs = _read_json(REPAIR_TASKS_PATH, {})
    baselines = _provider_baselines()
    vision_overrides = _read_json(VISION_OVERRIDES_PATH, {})
    items = []
    scrapers = {s.source_name: s for s in _official_scrapers()}
    for meta in _scraper_registry():
        st = _normalize_probe_status(scrapers[meta["name"]], store.get(meta["name"], {}))
        vi = vendor_info(meta["provider"], meta["provider"])
        rep = repairs.get(meta["name"], {})
        baseline = baselines.get(meta["provider"], {"rows": 0, "last_scraped": None})

        items.append({
            **meta,
            "vendor_zh": vi["zh"],
            "domain": vi["domain"],
            "status": st.get("status", "never"),
            "last_run": st.get("last_run"),
            "last_success": st.get("last_success"),
            "duration_ms": st.get("duration_ms"),
            "items": st.get("items"),
            "result": st.get("result"),
            "sample": st.get("sample", []),
            "message": st.get("message"),
            "error": st.get("error"),
            "data_rows": baseline["rows"],
            "data_last_scraped": baseline["last_scraped"],
            "repair": {
                "status": rep.get("status"),           # diagnosing/suggested/applied/failed
                "updated_at": rep.get("updated_at"),
                "summary": rep.get("summary"),
                # 有无可落地的配置建议,决定后台要不要显示「应用建议」
                "has_vision_fix": bool(
                    (rep.get("detail") or {}).get("suggested_tab_selectors")
                    or (rep.get("detail") or {}).get("suggested_screenshot_urls")
                    or (rep.get("detail") or {}).get("suggested_max_shots_per_page")
                ),
            } if rep else None,
            # 生效中的运行时覆盖配置(仅视觉采集器);None = 用代码里的默认值
            "vision_config": vision_overrides.get(meta["name"]),
        })
    return {
        "items": items,
        "pipeline": _read_json(settings.pipeline_health_path, {}),
        "runtime": {
            "playwright": settings.use_playwright,
            "chromium": _chromium_available(),
            "vision_credentials": _vision_credentials_available(),
        },
    }


def _persist_rows(name: str, rows: list) -> dict:
    """把单个抓取器的结果校验后增量写入 v2.sqlite（前端数据源）。

    只对 name 这一个源负责：该源本轮没再出现的价格方案会被软过期，其余厂商不受影响。
    """
    from app.db.sync_v2 import sync_v2_entries
    from app.models.canonical import is_official
    from app.pipeline.store import load_latest_snapshot, load_overrides
    from app.pipeline.validate import apply_overrides, validate_and_merge

    snap = load_latest_snapshot()
    previous = [e for e in (snap.entries if snap else []) if e.source == name]
    entries, _report = validate_and_merge(rows, previous, healthy_sources={name})
    overrides = [o for o in load_overrides() if o.source == name]
    if overrides:
        entries = apply_overrides(entries, overrides)
    entries = [e.model_copy(update={"official": is_official(e.channel, e.source)}) for e in entries]
    return sync_v2_entries(entries, scope_sources={name}, run_config={"trigger": "admin", "scraper": name})


async def _run_one_scraper(name: str, persist: bool = True) -> dict[str, Any]:
    """后台运行单个抓取器：抓取 + 解析 + 校验入库（persist=False 时只做解析验证）。"""
    target = None
    for s in _official_scrapers():
        if s.source_name == name:
            target = s
            break
    if target is None:
        raise HTTPException(404, f"未找到抓取器: {name}")

    unavailable_reason = _scraper_unavailable_reason(target)
    if unavailable_reason:
        _save_status(name, {
            "status": "unavailable", "result": "skipped", "last_run": _now(),
            "duration_ms": 0, "items": None, "error": None,
            "message": unavailable_reason,
        })
        return {"ok": False, "skipped": True, "message": unavailable_reason}

    # 人明确点了「运行」，就别被视觉降频拦下——降频是给定时管线省 token 的策略
    if getattr(target, "force_vision", None) is False:
        target.force_vision = True

    _save_status(name, {"status": "running", "result": "running", "last_run": _now(), "error": None, "message": None})
    started = time.time()
    try:
        from app.scrapers.vision_base import VisionSkipped

        try:
            rows = await target.fetch()
        except VisionSkipped as skipped:
            # 有意跳过 ≠ 跑了但没解析出数据。混为一谈会把正常跳过报成故障，
            # 用户看到「未解析出价格数据」只会以为脚本坏了。
            _save_status(name, {
                "status": "unavailable", "result": "skipped", "last_run": _now(),
                "duration_ms": int((time.time() - started) * 1000), "items": None,
                "error": None, "message": str(skipped),
            })
            return {"ok": False, "skipped": True, "message": str(skipped)}
        for r in rows:
            r.source = target.source_name
        elapsed = int((time.time() - started) * 1000)
        if rows:
            persisted: dict | None = None
            persist_error: str | None = None
            if persist:
                try:
                    # 同步 SQLAlchemy 写入放线程里，不阻塞事件循环
                    persisted = await asyncio.to_thread(_persist_rows, name, rows)
                except Exception as exc:  # 入库失败要显式暴露，不能假装成功
                    persist_error = f"{type(exc).__name__}: {exc}"
            if persist_error:
                _save_status(name, {
                    "status": "error", "result": "failed", "duration_ms": elapsed,
                    "items": len(rows), "error": persist_error,
                    "message": "解析成功但写入 v2.sqlite 失败",
                })
                return {"ok": False, "items": len(rows), "error": persist_error}
            msg = "本次验证成功，已解析出价格数据"
            if persisted:
                msg = (f"抓取并入库成功：新增 {persisted['new']} · 变更 {persisted['changed']} "
                       f"· 未变 {persisted['unchanged']} · 下线 {persisted['stale']}")
            _save_status(name, {
                "status": "ok", "result": "success", "last_success": _now(),
                "duration_ms": elapsed, "items": len(rows), "error": None,
                "sample": [{"model": r.model, "input": r.input_per_1m, "output": r.output_per_1m} for r in rows[:5]],
                "persisted": persisted,
                "message": msg,
            })
        else:
            _save_status(name, {
                "status": "warning", "result": "empty", "duration_ms": elapsed, "items": 0,
                "error": None,
                "message": "请求完成但未解析出价格数据；请检查页面结构或采集方式",
            })
        return {
            "ok": bool(rows), "warning": not rows, "items": len(rows), "duration_ms": elapsed,
            "persisted": persisted if rows else None,
            "sample": [
                {"model": r.model, "input": r.input_per_1m, "output": r.output_per_1m,
                 "currency": r.currency.value if hasattr(r.currency, "value") else str(r.currency)}
                for r in rows[:5]
            ],
        }
    except Exception as exc:
        elapsed = int((time.time() - started) * 1000)
        _save_status(name, {
            "status": "error", "result": "failed", "duration_ms": elapsed,
            "error": f"{type(exc).__name__}: {exc}",
            "message": "脚本执行抛出异常",
            "traceback": traceback.format_exc()[-2000:],
        })
        return {"ok": False, "error": repr(exc), "duration_ms": elapsed}


# 运行中的任务句柄,防止同一个脚本被重复触发
_running_tasks: dict[str, asyncio.Task] = {}
# 全局更新任务状态(后台跑,前端轮询)
_run_all_state: dict[str, Any] = {"running": False, "total": 0, "done": 0, "success": 0, "started_at": None}


@app.post("/api/admin/scrapers/{name}/run")
async def admin_run_scraper(name: str, admin: dict = Depends(require_admin)):
    if name in _running_tasks and not _running_tasks[name].done():
        return {"ok": False, "error": "该脚本正在运行中"}
    task = asyncio.create_task(_run_one_scraper(name))
    _running_tasks[name] = task
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=180)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "运行超时(180s),任务仍在后台继续"}


async def _run_all_bg() -> None:
    """全局更新：走完整抓取管线（抓取 → 校验合并 → override → 快照 → 入库）。

    旧实现是循环调 _run_one_scraper 只做解析，界面显示全部成功但一条都没入库；
    现在直接复用 pipeline.runner.run_once，与定时任务同一条链路，共用跨进程锁。
    """
    from app.pipeline.runner import run_once

    names = [m["name"] for m in _scraper_registry()]
    _run_all_state.update({
        "running": True, "total": len(names), "done": 0, "success": 0,
        "started_at": _now(), "error": None, "finished_at": None, "persisted": None,
    })

    def _on_progress(source: str, healthy: bool, items: int) -> None:
        _run_all_state["done"] += 1
        _run_all_state["current"] = source
        if healthy:
            _run_all_state["success"] += 1
        _save_status(source, {
            "status": "ok" if healthy else "warning",
            "result": "success" if healthy else "empty",
            "last_run": _now(),
            "items": items,
            "error": None,
            "message": "全局更新：已解析，等待本轮统一入库" if healthy else "全局更新：未解析出价格数据",
            # 不健康时不下发 last_success，避免把上一次成功时间冲成 None
            **({"last_success": _now()} if healthy else {}),
        })

    try:
        rc = await run_once(on_progress=_on_progress)
        _run_all_state["exit_code"] = rc
        if rc == 0:
            _run_all_state["message"] = "全局更新完成，快照与数据库已写入"
        elif rc == 2:
            _run_all_state["error"] = "已有另一条抓取管线运行中，本次跳过"
        else:
            _run_all_state["error"] = "本轮抓取结果为空，已保留上一快照"
    except Exception as exc:
        _run_all_state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _run_all_state["running"] = False
        _run_all_state["current"] = None
        _run_all_state["finished_at"] = _now()


@app.post("/api/admin/scrapers/run-all")
async def admin_run_all(admin: dict = Depends(require_admin)):
    """全局更新:后台任务,立即返回,前端轮询 run-all-status。"""
    if _run_all_state.get("running"):
        return {"ok": False, "error": "全局更新正在进行中", "state": _run_all_state}
    asyncio.create_task(_run_all_bg())
    return {"ok": True, "message": "全局更新已启动", "state": _run_all_state}


@app.get("/api/admin/scrapers/run-all-status")
def admin_run_all_status(admin: dict = Depends(require_admin)):
    return _run_all_state


@app.get("/api/admin/scrapers/{name}/detail")
def admin_scraper_detail(name: str, admin: dict = Depends(require_admin)):
    scraper = next((s for s in _official_scrapers() if s.source_name == name), None)
    if scraper is None:
        raise HTTPException(404, f"未找到采集器: {name}")
    path = _scraper_source_path(name)
    st = _normalize_probe_status(scraper, _status_store().get(name, {}))
    rep = _read_json(REPAIR_TASKS_PATH, {}).get(name, {})
    return {
        "status": st, "repair": rep, "source_file": str(path),
        "source_code": path.read_text(encoding="utf-8"),
        "collection_mode": scraper.collection_mode,
    }


class ScraperSourceInput(BaseModel):
    code: str = Field(min_length=20, max_length=100_000)
    reason: str = "manual edit"


@app.put("/api/admin/scrapers/{name}/source")
def admin_update_scraper_source(name: str, body: ScraperSourceInput, admin: dict = Depends(require_admin)):
    path = _scraper_source_path(name)
    try:
        compile(body.code, str(path), "exec")
    except SyntaxError as exc:
        raise HTTPException(422, f"Python 语法错误: 第 {exc.lineno} 行 {exc.msg}") from exc
    backup = path.with_suffix(f".bak-{int(time.time())}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(body.code, encoding="utf-8")
    _save_status(name, {"message": f"源码已保存，备份: {backup.name}", "source_updated_at": _now()})
    return {"ok": True, "backup": str(backup), "source_file": str(path)}


class ScraperGenerateInput(BaseModel):
    provider: str = Field(min_length=2, max_length=64)
    source_url: str = Field(min_length=8, max_length=512)
    scraper_name: str = Field(min_length=2, max_length=64)
    requirements: str = Field(min_length=5, max_length=10_000)
    existing_code: str = ""


@app.post("/api/admin/scrapers/generate")
async def admin_generate_scraper(body: ScraperGenerateInput, admin: dict = Depends(require_admin)):
    key = _pick_llm_key("repair")
    if not key:
        raise HTTPException(400, "请先在 API Key 中配置可用于代码生成的模型")
    prompt = f"""你是 Python 官方模型价格采集器工程师。请为真实的基座模型发行商官网编写一个可维护的采集器。
厂商: {body.provider}\n定价网址: {body.source_url}\n采集器名: {body.scraper_name}\n需求: {body.requirements}
现有草稿(可为空):\n{body.existing_code[:16000]}
必须继承 BaseScraper，返回 RawPrice，优先纯 HTTP，只有确实需要时才使用 Playwright；不得使用凭据、不得写数据库、不得执行 shell。
只返回完整 Python 源码，不要 markdown 代码围栏。"""
    code = (await _call_llm(key, prompt)).strip()
    if code.startswith("```"):
        code = code.strip("`").lstrip("python").strip()
    try:
        compile(code, f"{body.scraper_name}.py", "exec")
    except SyntaxError as exc:
        raise HTTPException(502, f"AI 返回的 Python 无法编译: {exc}") from exc
    drafts = _read_json(SCRAPER_DRAFTS_PATH, {})
    drafts[body.scraper_name] = {**body.model_dump(), "code": code, "updated_at": _now(), "status": "draft"}
    _write_json(SCRAPER_DRAFTS_PATH, drafts)
    return {"ok": True, "draft": drafts[body.scraper_name]}


@app.get("/api/admin/scraper-drafts")
def admin_scraper_drafts(admin: dict = Depends(require_admin)):
    return list(_read_json(SCRAPER_DRAFTS_PATH, {}).values())


class ScraperDraftSaveInput(BaseModel):
    code: str = Field(min_length=20, max_length=100_000)


@app.post("/api/admin/scraper-drafts/{draft_name}/save")
def admin_save_scraper_draft(draft_name: str, body: ScraperDraftSaveInput, admin: dict = Depends(require_admin)):
    """保存为待接入脚本，不自动加入 registry，需人工审核后登记。"""
    import re
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,48}", draft_name):
        raise HTTPException(422, "脚本名只能使用小写字母、数字和下划线，且必须以字母开头")
    try:
        compile(body.code, f"{draft_name}.py", "exec")
    except SyntaxError as exc:
        raise HTTPException(422, f"Python 语法错误: 第 {exc.lineno} 行 {exc.msg}") from exc
    path = Path(__file__).resolve().parent / "scrapers" / f"{draft_name}.py"
    if path.exists():
        raise HTTPException(409, f"脚本文件已存在: {path.name}")
    path.write_text(body.code, encoding="utf-8")
    drafts = _read_json(SCRAPER_DRAFTS_PATH, {})
    if draft_name in drafts:
        drafts[draft_name]["status"] = "saved_pending_registry"
        drafts[draft_name]["saved_file"] = str(path)
        drafts[draft_name]["updated_at"] = _now()
        _write_json(SCRAPER_DRAFTS_PATH, drafts)
    return {"ok": True, "file": str(path), "requires_registry_review": True}


OFFICIAL_VENDOR_CATALOG = [
    {"slug": "openai", "name": "OpenAI", "region": "US", "url": "https://openai.com/api/pricing/", "models": ["GPT", "o-series"]},
    {"slug": "anthropic", "name": "Anthropic", "region": "US", "url": "https://docs.anthropic.com/en/docs/about-claude/pricing", "models": ["Claude"]},
    {"slug": "google", "name": "Google DeepMind", "region": "US", "url": "https://ai.google.dev/pricing", "models": ["Gemini", "Gemma"]},
    {"slug": "meta", "name": "Meta", "region": "US", "url": "https://llama.meta.com/llama-downloads/", "models": ["Llama"]},
    {"slug": "xai", "name": "xAI", "region": "US", "url": "https://docs.x.ai/docs/models", "models": ["Grok"]},
    {"slug": "mistral", "name": "Mistral AI", "region": "FR", "url": "https://mistral.ai/technology/", "models": ["Mistral", "Codestral"]},
    {"slug": "cohere", "name": "Cohere", "region": "CA", "url": "https://docs.cohere.com/docs/models", "models": ["Command"]},
    {"slug": "deepseek", "name": "DeepSeek", "region": "CN", "url": "https://api-docs.deepseek.com/quick_start/pricing", "models": ["DeepSeek"]},
    {"slug": "moonshot", "name": "Moonshot AI", "region": "CN", "url": "https://platform.moonshot.cn/docs/pricing", "models": ["Kimi"]},
    {"slug": "minimax", "name": "MiniMax", "region": "CN", "url": "https://platform.minimaxi.com/docs/guides/pricing-paygo", "models": ["MiniMax"]},
    {"slug": "zhipu", "name": "Zhipu AI", "region": "CN", "url": "https://open.bigmodel.cn/pricing", "models": ["GLM"]},
    {"slug": "aliyun", "name": "Alibaba Cloud", "region": "CN", "url": "https://help.aliyun.com/zh/model-studio/model-pricing", "models": ["Qwen"]},
    {"slug": "bytedance", "name": "ByteDance", "region": "CN", "url": "https://www.volcengine.com/docs/82379/1099320", "models": ["Doubao", "Seed"]},
]


@app.get("/api/admin/coverage")
def admin_coverage(admin: dict = Depends(require_admin)):
    """LiteLLM 仅作覆盖面旁证；官方身份以厂商自有官网为准。"""
    latest = sorted(settings.snapshots_dir.glob("*.json"))
    entries = _read_json(latest[-1], {}).get("entries", []) if latest else []
    lite = [e for e in entries if e.get("source") == "litellm"]
    counts: dict[str, int] = {}
    for e in lite:
        provider = e.get("provider") or "unknown"
        counts[provider] = counts.get(provider, 0) + 1
    registered = {m["provider"] for m in _scraper_registry() if not m["is_vision"]}
    return {
        "source": "LiteLLM model_prices_and_context_window.json",
        "note": "LiteLLM 是聚合旁证，不等于官方发行商；官方采集器必须指向厂商自己的定价页。",
        "lite_model_count": len(lite),
        "lite_providers": sorted(({"provider": k, "models": v, "has_official_scraper": k in registered} for k, v in counts.items()), key=lambda x: -x["models"]),
        "official_catalog": [{**v, "has_scraper": v["slug"] in registered} for v in OFFICIAL_VENDOR_CATALOG],
    }


# ---------------------------------------------------------------------------
# 后台 · AI 修复
# ---------------------------------------------------------------------------

def _pick_llm_key(purpose: str = "repair") -> dict[str, Any] | None:
    keys = _read_json(LLM_KEYS_PATH, [])
    for k in keys:
        if k.get("enabled") and purpose in (k.get("purposes") or []):
            return k
    for k in keys:  # 没有指定用途的,取第一个可用的
        if k.get("enabled"):
            return k
    return None


def _llm_protocol(key: dict[str, Any]) -> str:
    """兼容旧 Key；Claude 官方配置默认走 Anthropic Messages 协议。"""
    protocol = key.get("protocol")
    if protocol in {"anthropic", "openai"}:
        return protocol
    base = (key.get("base_url") or "").lower()
    model = (key.get("model") or "").lower()
    return "anthropic" if "anthropic" in base or model.startswith("claude") else "openai"


async def _call_llm(key: dict[str, Any], prompt: str, images: list[bytes] | None = None) -> str:
    """按 Key 配置调用 Anthropic Messages 或 OpenAI 兼容协议。

    images 非空时走多模态:视觉抓取器的修复必须让模型看见页面现在长什么样,
    只给配置和报错它无从判断该截哪块、该点哪个 tab。
    """
    base = (key.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    model = key.get("model") or "gpt-4o-mini"
    protocol = _llm_protocol(key)
    shots = images or []
    if protocol == "anthropic":
        if shots:
            content: Any = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.standard_b64encode(png).decode("ascii"),
                    },
                }
                for png in shots
            ] + [{"type": "text", "text": prompt}]
        else:
            content = prompt
        payload = {
            "model": model,
            "max_tokens": int(key.get("max_tokens") or 8192),
            "messages": [{"role": "user", "content": content}],
        }
        headers = {
            "x-api-key": key["api_key"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        endpoint = f"{base}/v1/messages" if not base.endswith("/v1") else f"{base}/messages"
    else:
        if shots:
            oa_content: Any = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.standard_b64encode(png).decode("ascii")
                    },
                }
                for png in shots
            ] + [{"type": "text", "text": prompt}]
        else:
            oa_content = prompt
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": oa_content}],
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {key['api_key']}", "content-type": "application/json"}
        # 与 anthropic 分支同口径补全 /v1。base_url 填成站点根地址(不带 /v1)很常见，
        # 而不少网关对未知路径会返回 200 + 前端 HTML，raise_for_status 放行、解析 JSON
        # 才报错，排查时只看到一句 JSONDecodeError，极难定位。
        endpoint = (
            f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
        )

    async with httpx.AsyncClient(timeout=90) as cli:
        resp = await cli.post(endpoint, headers=headers, json=payload)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            ctype = resp.headers.get("content-type", "")
            raise RuntimeError(
                f"接口返回的不是 JSON(HTTP {resp.status_code} · {ctype} · {len(resp.content)} 字节)。"
                f"多半是 base_url 写错导致打到了网页而不是 API:{endpoint}"
            ) from None
        if protocol == "anthropic":
            blocks = data.get("content") or []
            text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
            if not text:
                raise RuntimeError("Anthropic 响应没有文本 content")
            return text
        return data["choices"][0]["message"]["content"]


_repair_tasks: dict[str, dict[str, Any]] = {}


def _set_repair_progress(task_id: str, **patch: Any) -> dict[str, Any]:
    task = _repair_tasks.setdefault(task_id, {})
    task.update(patch)
    task["updated_at"] = _now()
    return task


def _parse_llm_json(raw: str) -> dict[str, Any] | None:
    """从模型回复里抽出 JSON。模型常在 JSON 前后加一段说明("I'll analyze…")
    或裹上 markdown 代码块,直接 json.loads 会失败;失败后把原文当诊断展示,
    就会把模型的内心独白(还常常是英文)漏到后台界面上。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    return None


async def _capture_repair_shots(scraper: Any, limit: int = 3) -> list[bytes]:
    """给视觉修复取几张现场截图。只取前几张:一次修复没必要把整页都发给模型,
    页首通常就能看出 tab/布局变没变,张数多了纯粹烧钱。"""
    shots: list[bytes] = []
    async for png in scraper._capture():
        shots.append(png)
        if len(shots) >= limit:
            break
    return shots


async def _run_vision_repair(
    task_id: str, name: str, key: dict[str, Any], st: dict[str, Any], meta: dict[str, Any]
) -> None:
    """视觉抓取器的修复:不改解析代码,改「截哪儿、点哪儿、怎么问」。

    与脚本修复的区别在于喂给模型的东西:脚本修复给源码,视觉修复必须给现场截图——
    视觉路径本来就不依赖 DOM 结构,它坏掉通常是截错了区域或该点的 tab 没点到,
    只看配置和报错无从判断。
    """
    scraper = next((x for x in _official_scrapers() if x.source_name == name), None)
    if scraper is None:
        _set_repair_progress(task_id, status="failed", progress=100, message=f"未找到抓取器: {name}")
        return

    _set_repair_progress(task_id, status="collecting", progress=15, message="正在截取页面现状")
    try:
        shots = await _capture_repair_shots(scraper)
    except Exception as exc:
        _set_repair_progress(
            task_id, status="failed", progress=100, message=f"截图失败:{exc!r}"[:200]
        )
        return
    if not shots:
        _set_repair_progress(task_id, status="failed", progress=100, message="没截到任何画面,无法诊断")
        return

    config = {
        "source_url": getattr(scraper, "source_url", ""),
        "screenshot_urls": list(getattr(scraper, "screenshot_urls", []) or []),
        "tab_selectors": list(getattr(scraper, "tab_selectors", []) or []),
        "max_shots_per_page": getattr(scraper, "max_shots_per_page", None),
    }
    prompt = f"""你是网页视觉采集的调试专家。下面是某厂商官方定价页的实拍截图(按滚动顺序)。
这个采集器靠截图 + 多模态模型读价格,现在没读出数据。

【采集器】{name}({meta.get('class', '')})
【当前配置】
{json.dumps(config, ensure_ascii=False, indent=2)}
【最近状态】{st.get('status', '')} · {st.get('error') or st.get('message') or '(无错误信息)'}

请对照截图判断问题出在哪,并给出可直接落到配置里的修正。注意:
- 若价格表需要先点某个 tab/按钮才显示,请从截图里读出它的准确文案
- 若截图根本没拍到价格区(太靠上/太靠下/被弹窗挡住),请说明该怎么调整
- 若页面已改版到没有价格表,请直说,不要编造选择器

只输出 JSON(不要 markdown 代码块):
{{
  "diagnosis": "根因(中文,一句话)",
  "page_changed": true/false,
  "price_table_visible": true/false,
  "suggested_tab_selectors": ["从截图里读到的 tab 文案,没有则空数组"],
  "suggested_screenshot_urls": ["若应改截别的 URL 则给出,否则空数组(必须仍是本厂商域名)"],
  "suggested_max_shots_per_page": 1-30 的整数,若当前张数不足以覆盖整页则给出,否则 null,
  "fix_suggestion": "具体怎么改(中文)",
  "confidence": 0.0-1.0
}}"""

    _set_repair_progress(
        task_id, status="calling_llm", progress=40, message=f"已截 {len(shots)} 张,正在请模型看图诊断"
    )
    try:
        raw = await _call_llm(key, prompt, images=shots)
    except Exception as exc:
        _set_repair_progress(task_id, status="failed", progress=100, message=f"模型调用失败:{exc!r}"[:200])
        return

    _set_repair_progress(task_id, status="parsing", progress=80, message="正在解析模型返回结果")
    diag = _parse_llm_json(raw) or {
        "diagnosis": "模型返回的内容无法解析，请查看详情里的原文",
        "fix_suggestion": raw[:2000],
        "confidence": 0.0,
    }
    diag["repair_kind"] = "vision"
    diag["shots_used"] = len(shots)
    diag["current_config"] = config

    repair = {
        "status": "suggested",
        "kind": "vision",
        "updated_at": _now(),
        "summary": diag.get("diagnosis", ""),
        "detail": diag,
        "llm_key": key.get("name"),
        "task_id": task_id,
    }
    tasks = _read_json(REPAIR_TASKS_PATH, {})
    tasks[name] = repair
    _write_json(REPAIR_TASKS_PATH, tasks)
    _set_repair_progress(
        task_id, status="completed", progress=100,
        message=diag.get("diagnosis", "诊断完成"), result=diag,
    )


async def _run_ai_repair(task_id: str, name: str, key: dict[str, Any], st: dict[str, Any]) -> None:
    """后台执行 AI 诊断；HTTP 请求不等待 LLM 完成。"""
    try:
        meta = next((m for m in _scraper_registry() if m["name"] == name), {})
        if meta.get("is_vision"):
            await _run_vision_repair(task_id, name, key, st, meta)
            return
        _set_repair_progress(task_id, status="collecting", progress=15, message="读取脚本源码和最近错误")
        code = _scraper_source_path(name).read_text(encoding="utf-8")[:12000]
        prompt = f"""你是爬虫修复专家。以下 Python 抓取器从厂商官方定价页采集模型价格,现在失败了。

【脚本名】{name}({meta.get('class','')})
【目标页面】{meta.get('source_url','')}
【错误信息】{st.get('error','')}
【堆栈】{st.get('traceback','(无)')}

【脚本源码】
```python
{code}
```

请输出 JSON(不要 markdown 代码块):
{{
  "diagnosis": "失败根因(中文,一句话)",
  "page_changed": true/false,
  "fix_suggestion": "具体修复建议(中文,含关键代码片段)",
  "fixed_code": "如果可以直接给出修复后的完整源码则给出,否则为 null",
  "confidence": 0.0-1.0
}}"""
        _set_repair_progress(task_id, status="calling_llm", progress=35, message="正在调用模型分析脚本")
        raw = await _call_llm(key, prompt)
        _set_repair_progress(task_id, status="parsing", progress=78, message="正在解析模型返回结果")
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        diag = _parse_llm_json(raw) or {
            "diagnosis": "模型返回的内容无法解析，请查看详情里的原文",
            "fix_suggestion": raw[:2000],
            "fixed_code": None,
            "confidence": 0.0,
        }
        repair = {
            "status": "suggested", "updated_at": _now(),
            "summary": diag.get("diagnosis", ""), "detail": diag,
            "llm_key": key.get("name"), "task_id": task_id,
        }
        repairs = _read_json(REPAIR_TASKS_PATH, {})
        repairs[name] = repair
        _write_json(REPAIR_TASKS_PATH, repairs)
        _set_repair_progress(task_id, status="completed", progress=100, message="AI 修复建议已生成", repair=repair)
    except Exception as exc:
        repair = {"status": "failed", "updated_at": _now(), "summary": f"LLM 调用失败: {exc!r}", "task_id": task_id}
        repairs = _read_json(REPAIR_TASKS_PATH, {})
        repairs[name] = repair
        _write_json(REPAIR_TASKS_PATH, repairs)
        _set_repair_progress(task_id, status="failed", progress=100, message=str(exc)[:500], error=str(exc), repair=repair)


@app.post("/api/admin/scrapers/{name}/ai-repair", status_code=202)
async def admin_ai_repair(name: str, admin: dict = Depends(require_admin)):
    """启动后台 AI 诊断，立即返回 task_id；前端通过 status 接口轮询。"""
    scraper = next((s for s in _official_scrapers() if s.source_name == name), None)
    if scraper is None:
        raise HTTPException(404, f"未找到抓取器: {name}")
    st = _normalize_probe_status(scraper, _status_store().get(name, {}))
    is_vision = getattr(scraper, "source_name", "").startswith("vision-") or "vision" in type(scraper).__name__.lower()
    # 视觉采集器额外放行 unavailable / never:它们「没产出」往往正是要诊断的对象
    # (没配凭据被跳过、或截不到价格区),而截图诊断本身只需要 Playwright，不需要视觉凭据。
    allowed = {"warning", "error"} | ({"unavailable", "never"} if is_vision else set())
    if not st.get("error") and st.get("status") not in allowed:
        raise HTTPException(400, "该脚本没有可诊断的异常或空结果记录")
    key = _pick_llm_key("repair")
    if not key:
        raise HTTPException(400, "请先在「API Key」菜单录入可用的 LLM Key")
    for task in _repair_tasks.values():
        if task.get("name") == name and task.get("status") not in {"completed", "failed"}:
            return {"ok": True, "task_id": task["task_id"], "status": task}
    task_id = secrets.token_urlsafe(12)
    _set_repair_progress(task_id, name=name, status="queued", progress=5, message="已排队等待 AI 修复")
    asyncio.create_task(_run_ai_repair(task_id, name, key, st))
    return {"ok": True, "task_id": task_id, "status": _repair_tasks[task_id]}


_VISION_CONFIG_FIELDS = ("tab_selectors", "screenshot_urls", "max_shots_per_page")


class VisionConfigInput(BaseModel):
    """留空则采用该采集器最近一次 AI 视觉修复给出的建议。"""
    tab_selectors: list[str] | None = None
    screenshot_urls: list[str] | None = None
    max_shots_per_page: int | None = None


def _vision_scraper_or_404(name: str) -> Any:
    scraper = next((s for s in _official_scrapers() if s.source_name == name), None)
    if scraper is None:
        raise HTTPException(404, f"未找到抓取器: {name}")
    is_vision = name.startswith("vision-") or "vision" in type(scraper).__name__.lower()
    if not is_vision:
        raise HTTPException(400, "该接口只适用于视觉采集器")
    return scraper


def _validate_vision_config(scraper: Any, body: VisionConfigInput) -> dict[str, Any]:
    """校验要落盘的配置。建议来自大模型,不能直接照单全收。"""
    out: dict[str, Any] = {}

    if body.tab_selectors is not None:
        tabs = [t.strip() for t in body.tab_selectors if isinstance(t, str) and t.strip()]
        if len(tabs) > 20:
            raise HTTPException(400, "tab 数量过多(上限 20)")
        if any(len(t) > 40 for t in tabs):
            raise HTTPException(400, "tab 文案过长(单个上限 40 字符)")
        out["tab_selectors"] = tabs

    if body.screenshot_urls is not None:
        base_host = urlparse(getattr(scraper, "source_url", "")).netloc
        urls = [u.strip() for u in body.screenshot_urls if isinstance(u, str) and u.strip()]
        if len(urls) > 10:
            raise HTTPException(400, "URL 数量过多(上限 10)")
        for u in urls:
            parsed = urlparse(u)
            if parsed.scheme not in {"http", "https"}:
                raise HTTPException(400, f"非法 URL: {u}")
            # 不允许把采集目标改到别的站点:建议由大模型生成,必须锁在本厂商域名内,
            # 否则一次误判就能让采集器去抓任意网址。
            if base_host and parsed.netloc != base_host:
                raise HTTPException(400, f"URL 域名与该厂商不一致({parsed.netloc} != {base_host})")
        out["screenshot_urls"] = urls

    if body.max_shots_per_page is not None:
        shots = int(body.max_shots_per_page)
        if not 1 <= shots <= 30:
            raise HTTPException(400, "截图张数需在 1~30 之间")
        out["max_shots_per_page"] = shots

    if not out:
        raise HTTPException(400, "没有可应用的配置项")
    return out


@app.post("/api/admin/scrapers/{name}/vision-config")
def admin_apply_vision_config(
    name: str, body: VisionConfigInput, admin: dict = Depends(require_admin)
):
    """把 AI 视觉修复的建议落成运行时配置(不改代码,可随时回退)。"""
    scraper = _vision_scraper_or_404(name)

    if body.tab_selectors is None and body.screenshot_urls is None and body.max_shots_per_page is None:
        detail = ((_read_json(REPAIR_TASKS_PATH, {}).get(name) or {}).get("detail")) or {}
        if not detail:
            raise HTTPException(400, "该采集器还没有 AI 视觉修复建议,请先运行一次修复")
        body = VisionConfigInput(
            tab_selectors=detail.get("suggested_tab_selectors") or None,
            screenshot_urls=detail.get("suggested_screenshot_urls") or None,
            max_shots_per_page=detail.get("suggested_max_shots_per_page"),
        )

    config = _validate_vision_config(scraper, body)
    store = _read_json(VISION_OVERRIDES_PATH, {})
    previous = store.get(name) or {}
    # 合并而非整体替换:只改一项(比如单独调截图张数)不该把已生效的 tab / URL 冲掉。
    merged = {k: v for k, v in previous.items() if k in _VISION_CONFIG_FIELDS}
    merged.update(config)
    merged.update({"applied_at": _now(), "applied_by": admin.get("username", "")})
    store[name] = merged
    config = merged
    _write_json(VISION_OVERRIDES_PATH, store)

    tasks = _read_json(REPAIR_TASKS_PATH, {})
    if name in tasks:
        tasks[name]["status"] = "applied"
        tasks[name]["applied_at"] = config["applied_at"]
        _write_json(REPAIR_TASKS_PATH, tasks)
    return {"ok": True, "applied": config, "previous": previous}


@app.delete("/api/admin/scrapers/{name}/vision-config")
def admin_revert_vision_config(name: str, admin: dict = Depends(require_admin)):
    """回退到代码里写死的默认配置。"""
    _vision_scraper_or_404(name)
    store = _read_json(VISION_OVERRIDES_PATH, {})
    removed = store.pop(name, None)
    if removed is None:
        raise HTTPException(404, "该采集器没有生效中的覆盖配置")
    _write_json(VISION_OVERRIDES_PATH, store)
    return {"ok": True, "removed": removed}


@app.get("/api/admin/scrapers/{name}/ai-repair/{task_id}")
def admin_ai_repair_status(name: str, task_id: str, admin: dict = Depends(require_admin)):
    task = _repair_tasks.get(task_id)
    if not task or task.get("name") != name:
        raise HTTPException(404, "AI 修复任务不存在或已过期")
    return task


@app.post("/api/admin/scrapers/{name}/apply-fix")
async def admin_apply_fix(name: str, admin: dict = Depends(require_admin)):
    """把 AI 给出的 fixed_code 写回脚本文件(先备份)。"""
    rep = _read_json(REPAIR_TASKS_PATH, {}).get(name, {})
    code = (rep.get("detail") or {}).get("fixed_code")
    if not code:
        raise HTTPException(400, "没有可应用的修复代码")
    try:
        path = _scraper_source_path(name)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(404, "找不到脚本文件") from exc
    backup = path.with_suffix(f".bak-{int(time.time())}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(code, encoding="utf-8")
    repairs = _read_json(REPAIR_TASKS_PATH, {})
    repairs[name]["status"] = "applied"
    repairs[name]["updated_at"] = _now()
    repairs[name]["backup"] = str(backup)
    _write_json(REPAIR_TASKS_PATH, repairs)
    return {"ok": True, "backup": str(backup)}


# ---------------------------------------------------------------------------
# 后台 · 厂商信息维护
# ---------------------------------------------------------------------------

class VendorInfoInput(BaseModel):
    zh: str = ""
    domain: str = ""


@app.put("/api/admin/vendors/{slug}")
def admin_update_vendor(slug: str, body: VendorInfoInput, admin: dict = Depends(require_admin)):
    overrides = _read_json(VENDOR_INFO_PATH, {})
    overrides[slug] = {"zh": body.zh, "domain": body.domain}
    _write_json(VENDOR_INFO_PATH, overrides)
    return {"ok": True, "slug": slug, **overrides[slug]}


# ---------------------------------------------------------------------------
# 后台 · LLM API Key 管理
# ---------------------------------------------------------------------------

class LLMKeyInput(BaseModel):
    name: str
    protocol: str = Field(default="openai", pattern="^(openai|anthropic)$")
    base_url: str = "https://api.openai.com/v1"
    api_key: str
    model: str = "gpt-4o-mini"
    max_tokens: int = Field(default=8192, ge=256, le=65536)
    purposes: list[str] = Field(default_factory=lambda: ["repair"])  # repair/ocr/monitor
    enabled: bool = True


def _mask(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


@app.get("/api/admin/llm-keys")
def llm_keys_list(admin: dict = Depends(require_admin)):
    keys = _read_json(LLM_KEYS_PATH, [])
    return [
        {**k, "protocol": _llm_protocol(k), "api_key": _mask(k.get("api_key", "")), "id": k.get("id")}
        for k in keys
    ]


@app.post("/api/admin/llm-keys")
def llm_keys_create(body: LLMKeyInput, admin: dict = Depends(require_admin)):
    keys = _read_json(LLM_KEYS_PATH, [])
    item = {"id": secrets.token_urlsafe(8), **body.model_dump(), "created_at": _now()}
    keys.append(item)
    _write_json(LLM_KEYS_PATH, keys)
    return {"ok": True, "id": item["id"]}


@app.put("/api/admin/llm-keys/{kid}")
def llm_keys_update(kid: str, body: LLMKeyInput, admin: dict = Depends(require_admin)):
    keys = _read_json(LLM_KEYS_PATH, [])
    for k in keys:
        if k.get("id") == kid:
            # api_key 传掩码或空则保留原值
            patch = body.model_dump()
            if not patch.get("api_key") or "*" in patch["api_key"]:
                patch["api_key"] = k.get("api_key")
            k.update(patch)
            _write_json(LLM_KEYS_PATH, keys)
            return {"ok": True}
    raise HTTPException(404, "Key 不存在")


@app.delete("/api/admin/llm-keys/{kid}")
def llm_keys_delete(kid: str, admin: dict = Depends(require_admin)):
    keys = _read_json(LLM_KEYS_PATH, [])
    new = [k for k in keys if k.get("id") != kid]
    if len(new) == len(keys):
        raise HTTPException(404, "Key 不存在")
    _write_json(LLM_KEYS_PATH, new)
    return {"ok": True}


class FetchModelsInput(BaseModel):
    base_url: str = ""
    api_key: str = ""
    key_id: str = ""   # 编辑已有 Key 时不必重新填 Key，按 id 取库里那把


@app.post("/api/admin/llm-keys/fetch-models")
async def admin_fetch_models(body: FetchModelsInput, admin: dict = Depends(require_admin)):
    """拉取该网关可用的模型列表，供表单下拉选择，避免手输模型名打错。"""
    base = (body.base_url or "").strip().rstrip("/")
    api_key = (body.api_key or "").strip()
    # 列表接口返回的 api_key 是打码的(sk-e****6e)，编辑表单里拿到的就是这个值。
    # 所以只要带了 key_id，且用户没重新输入一把完整的 Key，就用库里的真值。
    if body.key_id and (not api_key or "*" in api_key):
        stored = next(
            (k for k in _read_json(LLM_KEYS_PATH, []) if k.get("id") == body.key_id), None
        )
        if stored:
            api_key = stored.get("api_key", "")
            base = base or (stored.get("base_url") or "").rstrip("/")
    if not base or not api_key:
        raise HTTPException(400, "请先填写 Base URL 和 API Key")

    url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    try:
        async with httpx.AsyncClient(timeout=30) as cli:
            resp = await cli.get(
                url, headers={"Authorization": f"Bearer {api_key}", "x-api-key": api_key}
            )
            resp.raise_for_status()
            data = resp.json()
    except ValueError:
        raise HTTPException(502, f"接口返回的不是 JSON，请检查 Base URL:{url}") from None
    except Exception as exc:
        raise HTTPException(502, f"{type(exc).__name__}:{exc}"[:200]) from None

    raw = data.get("data") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise HTTPException(502, "返回格式无法识别，没有模型列表")
    models = sorted(
        {(m.get("id") or m.get("name") or "") if isinstance(m, dict) else str(m) for m in raw} - {""}
    )
    return {"ok": True, "url": url, "models": models}


@app.post("/api/admin/llm-keys/{kid}/test")
async def llm_keys_test(kid: str, admin: dict = Depends(require_admin)):
    keys = _read_json(LLM_KEYS_PATH, [])
    key = next((k for k in keys if k.get("id") == kid), None)
    if not key:
        raise HTTPException(404, "Key 不存在")
    try:
        out = await _call_llm(key, "回复 ok 两个字即可。")
        return {"ok": True, "reply": out[:100]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

def _frontend_page() -> str:
    built = FRONTEND_DIST / "index.html"
    if built.exists():
        return built.read_text(encoding="utf-8")
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def page_index():
    return _frontend_page()


@app.get("/admin", response_class=HTMLResponse)
def page_admin():
    # 必须返回后台页。此前误用 _frontend_page()(与首页同一函数),导致 /admin 打开的是
    # 前台价格浏览页,后台完全进不去。frontend/ 下的 Vue AdminView 目前仍是骨架,
    # 真正可用的后台是 app/web/official/admin.html,故这里直接返回它。
    return (WEB_DIR / "admin.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
