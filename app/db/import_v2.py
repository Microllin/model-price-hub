"""把最新快照 JSON + 新版 scraper 离线解析结果导入 schema_v2 四表（SQLite）。

⚠️ 一次性/离线重建工具，会 drop 全表。日常增量写库请用 app/db/sync_v2.py 的
   sync_v2_entries()——它按 uq_price_plan 维度键 upsert，被抓取管线和 admin
   「运行脚本 / 全局更新」自动调用。本脚本只在需要从快照彻底重建 v2.sqlite 时使用。

用法:
    python -m app.db.import_v2

流程:
  1. 读 data/snapshots/ 下最新 JSON 作为基础数据
  2. 用新版 scraper 离线解析 fixture，产出更完整的 official 记录（含缓存价等）
  3. 以 scraper 产出覆盖旧快照中同 provider+channel=official 的记录
  4. 建表（drop + create）→ 写入 providers → models → price_plans → scrape_runs
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.schema_v2 import (
    Base, BillingUnit, CurrencyEnum, Model, PricePlan, Provider,
    ProviderType, RegionEnum, ScrapeRun, _tw_hash,
)

DB_PATH = Path("data/v2.sqlite")
SNAP_DIR = Path("data/snapshots")

# 厂商类型映射
_VENDOR_TYPE = {
    "openrouter": ProviderType.AGGREGATOR,
    "litellm": ProviderType.AGGREGATOR,
    "siliconflow": ProviderType.RESELLER,
    "ppio": ProviderType.RESELLER,
}


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _billing(v: str) -> BillingUnit:
    mapping = {
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
    return mapping.get(v, BillingUnit.TOKEN)


def _region(v: str) -> RegionEnum:
    return RegionEnum.CN if v == "cn" else RegionEnum.INTL


def _currency(v: str) -> CurrencyEnum:
    return CurrencyEnum.CNY if v == "CNY" else CurrencyEnum.USD


def _scraper_overrides() -> list[dict]:
    """用新版 scraper 离线解析 fixture，产出更完整的 official 记录。"""
    from tests.conftest import read_fixture
    from app.scrapers.zhipu import ZhipuScraper
    from app.scrapers.minimax import MiniMaxScraper
    from app.scrapers.iflytek import IflytekScraper
    from app.scrapers.yi import YiScraper
    from app.scrapers.kimi import KimiScraper

    overrides: list[dict] = []

    parsers = [
        ("zhipu_body.txt", ZhipuScraper()),
        ("minimax_body.txt", MiniMaxScraper()),
        ("iflytek_body.txt", IflytekScraper()),
        ("yi_body.txt", YiScraper()),
    ]
    for fixture, scraper in parsers:
        try:
            rows = scraper.parse(read_fixture(fixture))
            for r in rows:
                d = r.model_dump()
                d["source"] = scraper.source_name
                d["source_url"] = scraper.source_url
                overrides.append(d)
        except Exception as e:
            print(f"  ⚠️ {fixture}: {e}")

    # Kimi: 多个 fixture
    kimi = KimiScraper()
    for fx in ["kimi_chat-k3.md", "kimi_chat-k27-code.md", "kimi_chat-k26.md",
               "kimi_chat-k25.md", "kimi_chat-v1.md", "kimi_batch.md", "kimi_tools.md"]:
        try:
            rows = kimi.parse(read_fixture(fx))
            for r in rows:
                d = r.model_dump()
                d["source"] = kimi.source_name
                d["source_url"] = kimi.source_url
                overrides.append(d)
        except Exception:
            pass

    print(f"  Scraper 离线解析产出: {len(overrides)} 条")
    return overrides


def main():
    # 找最新快照
    snaps = sorted(SNAP_DIR.glob("*.json"))
    if not snaps:
        print("No snapshots found"); sys.exit(1)
    latest = snaps[-1]
    print(f"导入快照: {latest}")

    with open(latest) as f:
        data = json.load(f)
    entries = data.get("entries", [])
    print(f"  快照: {len(entries)} 条")

    # 获取新版 scraper 离线解析结果
    overrides = _scraper_overrides()

    # 合并：scraper 产出覆盖快照中同 provider+channel=official 的记录
    override_providers = set()
    for o in overrides:
        if o.get("channel") == "official":
            override_providers.add(o["provider"])

    # 从快照中剥离被覆盖的 provider+official 记录
    kept = []
    removed = 0
    for e in entries:
        if e.get("provider") in override_providers and e.get("channel") == "official":
            removed += 1
            continue
        kept.append(e)
    print(f"  快照保留: {len(kept)} 条（剥离 {removed} 条旧 official）")

    # 合并
    all_entries = kept + overrides
    print(f"  合并后总计: {len(all_entries)} 条")

    # 建库
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    with Session(engine) as s:
        # --- 1. providers ---
        provider_cache: dict[str, int] = {}
        for e in all_entries:
            slug = e["provider"]
            if slug not in provider_cache:
                p = Provider(
                    slug=slug,
                    display_name=slug.replace("_", " ").title(),
                    type=_VENDOR_TYPE.get(slug, ProviderType.VENDOR),
                    region="both",
                )
                s.add(p)
                s.flush()
                provider_cache[slug] = p.id

        # --- 2. models ---
        model_cache: dict[tuple[str, str], int] = {}
        for e in all_entries:
            slug = e["provider"]
            mid = e["model"]
            key = (slug, mid)
            if key not in model_cache:
                m = Model(
                    provider_id=provider_cache[slug],
                    model_id=mid,
                    canonical=e.get("canonical_model", ""),
                    modality=e.get("modality", "text"),
                    context_window=e.get("context_window"),
                    max_output=e.get("max_output"),
                )
                s.add(m)
                s.flush()
                model_cache[key] = m.id

        # --- 3. price_plans ---
        plan_count = 0
        for e in all_entries:
            slug = e["provider"]
            mid = e["model"]
            tw = e.get("time_window")
            pp = PricePlan(
                model_id=model_cache[(slug, mid)],
                channel=e.get("channel", "official"),
                currency=_currency(e.get("currency", "CNY")),
                region=_region(e.get("region", "cn")),
                billing_unit=_billing(e.get("billing_unit", "token")),
                service_tier=e.get("service_tier", "standard"),
                cache_state=e.get("cache_state"),
                context_range=e.get("context_range"),
                deployment_version=e.get("deployment_version"),
                time_window=tw,
                tw_hash=_tw_hash(tw),
                input_price=_dec(e.get("input_per_1m")),
                output_price=_dec(e.get("output_per_1m")),
                cached_read=_dec(e.get("cached_input_per_1m")),
                cached_write=_dec(e.get("cache_write_per_1m")),
                source=e.get("source", ""),
                source_url=e.get("source_url", ""),
                scraped_at=datetime.now(timezone.utc),
            )
            s.add(pp)
            plan_count += 1

        # --- 4. scrape_run ---
        run = ScrapeRun(
            run_date=date.today(),
            total_scraped=plan_count,
            new_count=plan_count,
        )
        s.add(run)

        s.commit()
        print(f"\n✅ 导入完成:")
        print(f"  providers:   {len(provider_cache)}")
        print(f"  models:      {len(model_cache)}")
        print(f"  price_plans: {plan_count}")
        print(f"  DB: {DB_PATH}")


if __name__ == "__main__":
    main()
