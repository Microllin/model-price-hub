"""价格查询路由。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api import repository as repo
from app.api.confidence import official_prices
from app.config import settings
from app.models.pricing import PriceEntry

router = APIRouter(prefix="/v1", tags=["prices"])


def _meta(entries: list[PriceEntry]) -> dict:
    return {
        "data_date": repo.data_date(),
        "count": len(entries),
        "fx_usd_to_cny": settings.usd_to_cny,
    }


@router.get("/prices")
def get_prices(
    provider: str | None = None,
    channel: str | None = None,
    model: str | None = None,
    region: str | None = Query(None, pattern="^(cn|intl)$"),
    currency: str | None = Query(None, pattern="^(USD|CNY)$"),
    official: bool | None = Query(None, description="true=仅官方渠道, false=仅非官方"),
    convert: str | None = Query(None, pattern="^(USD|CNY)$"),
):
    entries = repo.filter_entries(
        repo.load_entries(),
        provider=provider, channel=channel, model=model,
        region=region, currency=currency,
    )
    if official is not None:
        entries = [e for e in entries if e.official == official]
    if convert:
        entries = [repo.convert_currency(e, convert) for e in entries]
    return {**_meta(entries), "entries": entries}


@router.get("/official-prices")
def get_official_prices(
    provider: str | None = None,
    model: str | None = None,
    region: str | None = Query(None, pattern="^(cn|intl)$"),
    currency: str | None = Query(None, pattern="^(USD|CNY)$"),
    confidence: str | None = Query(None, pattern="^(high|medium|low)$"),
):
    """以模型为主体的官方价 + 多源交叉验证置信度。"""
    rows = official_prices(repo.load_entries())
    if provider:
        rows = [r for r in rows if r["provider"] == provider]
    if model:
        rows = [r for r in rows if model.lower() in r["canonical_model"].lower()]
    if region:
        rows = [r for r in rows if r["region"] == region]
    if currency:
        rows = [r for r in rows if r["currency"] == currency]
    if confidence:
        rows = [r for r in rows if r["confidence"] == confidence]
    return {
        "data_date": repo.data_date(),
        "count": len(rows),
        "fx_usd_to_cny": settings.usd_to_cny,
        "entries": rows,
    }


@router.get("/prices/{provider}/{model}")
def get_model_prices(provider: str, model: str):
    """某模型的全部渠道/货币变体。"""
    entries = repo.filter_entries(repo.load_entries(), provider=provider)
    entries = [e for e in entries if e.model == model]
    if not entries:
        raise HTTPException(status_code=404, detail=f"未找到 {provider}/{model}")
    return {**_meta(entries), "entries": entries}


@router.get("/providers")
def list_providers():
    entries = repo.load_entries()
    out: dict[str, set[str]] = {}
    for e in entries:
        out.setdefault(e.provider, set()).add(e.channel)
    return {
        "data_date": repo.data_date(),
        "providers": [
            {"provider": p, "channels": sorted(ch)} for p, ch in sorted(out.items())
        ],
    }


@router.get("/models")
def list_models(provider: str | None = None):
    entries = repo.filter_entries(repo.load_entries(), provider=provider)
    out: dict[tuple[str, str], dict] = {}
    for e in entries:
        k = (e.provider, e.model)
        d = out.setdefault(
            k, {"provider": e.provider, "model": e.model, "channels": set(), "currencies": set()}
        )
        d["channels"].add(e.channel)
        d["currencies"].add(e.currency.value)
    models = [
        {**v, "channels": sorted(v["channels"]), "currencies": sorted(v["currencies"])}
        for v in out.values()
    ]
    return {"data_date": repo.data_date(), "count": len(models), "models": models}
