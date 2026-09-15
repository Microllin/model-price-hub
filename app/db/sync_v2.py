"""把 PriceEntry 增量写入 schema_v2 四表（data/v2.sqlite）—— 前端 serve_official 的数据源。

与 app/db/import_v2.py 的区别：
  - import_v2 是一次性离线导入：drop_all + create_all + 全量插入，只能手工跑。
  - 本模块是增量 upsert：按 uq_price_plan 维度键更新已有行，不删表，可被 pipeline
    与 admin「运行脚本」按单个数据源反复调用。

软过期语义：
  一轮同步只对「本轮实际抓取过的数据源」(scope_sources) 负责。这些源下本轮没再出现
  的 price_plan 会被置为 is_current=False + effective_to=now，其余源原封不动。
  这样按单个厂商跑脚本时，不会把别家的价格误判为下线。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.pricing import PriceEntry
from app.models.schema_v2 import (
    Base,
    BillingUnit,
    CurrencyEnum,
    Model,
    PricePlan,
    Provider,
    ProviderType,
    RegionEnum,
    ScrapeRun,
    _tw_hash,
)

DB_PATH = Path("data/v2.sqlite")

# 厂商类型映射（与 import_v2 保持一致）
_VENDOR_TYPE = {
    "openrouter": ProviderType.AGGREGATOR,
    "litellm": ProviderType.AGGREGATOR,
    "siliconflow": ProviderType.RESELLER,
    "ppio": ProviderType.RESELLER,
}

_BILLING_MAP = {
    "token": BillingUnit.TOKEN,
    "request": BillingUnit.REQUEST,
    "second": BillingUnit.SECOND,
    "minute": BillingUnit.MINUTE,
    "per_image": BillingUnit.PER_IMAGE,
    "per_voice": BillingUnit.PER_VOICE,
    "10k_chars": BillingUnit.CHARS_10K,
    "10k_chats": BillingUnit.CHATS_10K,
    "gb_hour": BillingUnit.GB_HOUR,
}


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _billing(v: str) -> BillingUnit:
    return _BILLING_MAP.get(v, BillingUnit.TOKEN)


def _region(v) -> RegionEnum:
    v = getattr(v, "value", v)
    return RegionEnum.CN if v == "cn" else RegionEnum.INTL


def _currency(v) -> CurrencyEnum:
    v = getattr(v, "value", v)
    return CurrencyEnum.CNY if v == "CNY" else CurrencyEnum.USD


def get_engine(db_path: Path | None = None):
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", echo=False)
    Base.metadata.create_all(engine)  # 建缺失的表，不动已有数据
    return engine


def _plan_key(e: PriceEntry) -> tuple:
    """与 uq_price_plan 一致的维度键（model_id 换成 provider+model 文本）。"""
    return (
        e.provider,
        e.model,
        e.channel,
        _currency(e.currency).value,
        e.service_tier,
        e.cache_state,
        e.context_range,
        e.deployment_version,
        _billing(e.billing_unit).value,
        e.source,
        _tw_hash(e.time_window),
    )


def sync_v2_entries(
    entries: Iterable[PriceEntry],
    *,
    scope_sources: set[str] | None = None,
    db_path: Path | None = None,
    run_config: dict | None = None,
) -> dict:
    """增量把 entries upsert 进 v2.sqlite，返回统计字典。

    scope_sources:
        本轮负责的数据源集合。None = 全量同步（所有源都参与软过期判定）。
        传单个源时只影响该源的行，其余源保持不变。
    """
    rows = list(entries)
    started = datetime.now(timezone.utc)
    engine = get_engine(db_path)

    stats = {
        "scraped": len(rows),
        "new": 0,
        "changed": 0,
        "unchanged": 0,
        "stale": 0,
        "providers": 0,
        "models": 0,
    }

    if scope_sources is None:
        scope_sources = {e.source for e in rows if e.source}

    with Session(engine) as s:
        # --- 1. providers：按 slug upsert ---
        providers = {p.slug: p for p in s.scalars(select(Provider)).all()}
        for e in rows:
            if e.provider not in providers:
                p = Provider(
                    slug=e.provider,
                    display_name=e.provider.replace("_", " ").title(),
                    type=_VENDOR_TYPE.get(e.provider, ProviderType.VENDOR),
                    region="both",
                )
                s.add(p)
                s.flush()
                providers[e.provider] = p
                stats["providers"] += 1

        # --- 2. models：按 (provider_id, model_id) upsert，匹配时忽略大小写 ---
        # 不同来源写同一个模型的大小写常常不一致(网页解析拿到 GLM-5.3，
        # 视觉识别吐出 glm-5.3)。按原样精确匹配会把同一个模型建成两条记录，
        # 前台就会并排出现两行一模一样的价格，模型总数也虚高。
        # 这里按小写匹配，命中后沿用库里已有的写法，不改动已有记录。
        models: dict[tuple[int, str], Model] = {
            (m.provider_id, m.model_id.lower()): m for m in s.scalars(select(Model)).all()
        }
        for e in rows:
            pid = providers[e.provider].id
            key = (pid, (e.model or "").lower())
            m = models.get(key)
            if m is None:
                m = Model(
                    provider_id=pid,
                    model_id=e.model,
                    canonical=e.canonical_model or "",
                    modality=e.modality or "text",
                    context_window=e.context_window,
                    max_output=e.max_output,
                )
                s.add(m)
                s.flush()
                models[key] = m
                stats["models"] += 1
            else:
                # 补齐后来才抓到的元信息，不用空值覆盖已有值
                if e.canonical_model and m.canonical != e.canonical_model:
                    m.canonical = e.canonical_model
                if e.modality and m.modality != e.modality:
                    m.modality = e.modality
                if e.context_window and m.context_window != e.context_window:
                    m.context_window = e.context_window
                if e.max_output and m.max_output != e.max_output:
                    m.max_output = e.max_output
                if not m.is_active:
                    m.is_active = True

        # --- 3. price_plans：按 uq_price_plan upsert ---
        model_id_by_db_id = {m.id: m for m in models.values()}
        existing: dict[tuple, PricePlan] = {}
        for pp in s.scalars(select(PricePlan)).all():
            m = model_id_by_db_id.get(pp.model_id)
            if m is None:
                continue
            slug = next((k for k, v in providers.items() if v.id == m.provider_id), None)
            if slug is None:
                continue
            existing[(
                slug, m.model_id, pp.channel,
                getattr(pp.currency, "value", pp.currency),
                pp.service_tier, pp.cache_state, pp.context_range,
                pp.deployment_version,
                getattr(pp.billing_unit, "value", pp.billing_unit),
                pp.source, pp.tw_hash,
            )] = pp

        seen: set[tuple] = set()
        for e in rows:
            key = _plan_key(e)
            seen.add(key)
            pid = providers[e.provider].id
            mdl = models[(pid, e.model)]
            new_vals = {
                "input_price": _dec(e.input_per_1m),
                "output_price": _dec(e.output_per_1m),
                "cached_read": _dec(e.cached_input_per_1m),
                "cached_write": _dec(e.cache_write_per_1m),
            }
            pp = existing.get(key)
            if pp is None:
                pp = PricePlan(
                    model_id=mdl.id,
                    channel=e.channel,
                    currency=_currency(e.currency),
                    region=_region(e.region),
                    billing_unit=_billing(e.billing_unit),
                    service_tier=e.service_tier,
                    cache_state=e.cache_state,
                    context_range=e.context_range,
                    deployment_version=e.deployment_version,
                    time_window=e.time_window,
                    tw_hash=_tw_hash(e.time_window),
                    source=e.source,
                    source_url=e.source_url or "",
                    effective_from=e.effective_from,
                    scraped_at=e.scraped_at or started,
                    is_current=True,
                    **new_vals,
                )
                s.add(pp)
                s.flush()
                existing[key] = pp
                stats["new"] += 1
            else:
                changed = any(getattr(pp, f) != v for f, v in new_vals.items())
                for f, v in new_vals.items():
                    setattr(pp, f, v)
                pp.region = _region(e.region)
                pp.source_url = e.source_url or pp.source_url
                pp.scraped_at = e.scraped_at or started
                pp.is_current = True
                pp.effective_to = None
                stats["changed" if changed else "unchanged"] += 1

        # --- 4. 软过期：本轮负责的源里没再出现的方案 ---
        for key, pp in existing.items():
            if key in seen:
                continue
            if pp.source not in scope_sources:
                continue  # 不是本轮负责的源，不碰
            if pp.is_current:
                pp.is_current = False
                pp.effective_to = started
                stats["stale"] += 1

        # --- 5. 运行记录 ---
        finished = datetime.now(timezone.utc)
        s.add(ScrapeRun(
            run_date=date.today(),
            started_at=started,
            finished_at=finished,
            total_scraped=stats["scraped"],
            new_count=stats["new"],
            changed_count=stats["changed"],
            stale_count=stats["stale"],
            error_count=0,
            run_config=run_config or {"scope_sources": sorted(scope_sources)},
        ))
        s.commit()

    return stats
