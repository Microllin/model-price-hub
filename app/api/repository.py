"""数据访问层:API 从这里取数。优先读 DB,DB 空则回退 latest.json 快照。"""
from __future__ import annotations

from app.config import settings
from app.db.session import all_entries as db_entries
from app.models.pricing import Currency, PriceEntry
from app.pipeline.store import load_latest_snapshot


def load_entries() -> list[PriceEntry]:
    rows = db_entries()
    if rows:
        return rows
    snap = load_latest_snapshot()
    return snap.entries if snap else []


def data_date() -> str | None:
    snap = load_latest_snapshot()
    return snap.data_date if snap else None


def filter_entries(
    entries: list[PriceEntry],
    *,
    provider: str | None = None,
    channel: str | None = None,
    model: str | None = None,
    region: str | None = None,
    currency: str | None = None,
) -> list[PriceEntry]:
    def ok(e: PriceEntry) -> bool:
        if provider and e.provider != provider:
            return False
        if channel and e.channel != channel:
            return False
        if model and model.lower() not in e.model.lower():
            return False
        if region and e.region.value != region:
            return False
        if currency and e.currency.value != currency:
            return False
        return True

    return [e for e in entries if ok(e)]


def convert_currency(e: PriceEntry, target: str) -> PriceEntry:
    """近似汇率换算,仅用于展示。原生货币始终是 source of truth。"""
    if e.currency.value == target:
        return e
    rate = settings.usd_to_cny
    if e.currency == Currency.USD and target == "CNY":
        factor = rate
    elif e.currency == Currency.CNY and target == "USD":
        factor = 1 / rate
    else:
        return e

    def conv(v: float | None) -> float | None:
        return round(v * factor, 4) if v is not None else None

    return e.model_copy(
        update={
            "currency": Currency(target),
            "input_per_1m": conv(e.input_per_1m),
            "output_per_1m": conv(e.output_per_1m),
            "cached_input_per_1m": conv(e.cached_input_per_1m),
            "cache_write_per_1m": conv(e.cache_write_per_1m),
        }
    )
