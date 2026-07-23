"""抓取管线编排 + CLI 入口。

流程:并发抓取 → 校验合并(对比上一快照)→ 叠加 override 层 → 写快照 + 入库 → 打印变更报告。
单个抓取器异常不影响整体;整体抓取结果为空时保留上一快照,不覆盖。

用法:
  python -m app.pipeline.runner            # 跑一次
  python -m app.pipeline.runner --dry-run  # 只抓取+校验,不写库/快照
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import settings
from app.db.session import upsert_entries
from app.models.pricing import RawPrice
from app.pipeline.store import load_latest_snapshot, load_overrides, write_snapshot
from app.pipeline.validate import apply_overrides, validate_and_merge
from app.scrapers.registry import all_scrapers


async def _run_scraper(scraper) -> list[RawPrice]:
    name = scraper.__class__.__name__
    try:
        rows = await scraper.fetch()
        for r in rows:              # 标记数据源,供多源交叉验证
            r.source = scraper.source_name
        print(f"  [ok]   {name}: {len(rows)} 条")
        return rows
    except Exception as exc:  # 单个失败不影响整体
        print(f"  [fail] {name}: {exc!r}", file=sys.stderr)
        return []


async def collect() -> list[RawPrice]:
    scrapers = all_scrapers()
    # 渲染类抓取器各自 launch 一个 Chromium,并发跑会把整机内存撑爆(历史峰值 ~15G / OOM)。
    # 用信号量把「同时在跑的浏览器数」压到 render_concurrency(默认 1);HTTP 源不受此限,仍全并发。
    render_sem = asyncio.Semaphore(max(1, settings.render_concurrency))

    async def _guarded(s) -> list[RawPrice]:
        if getattr(s, "requires_render", False):
            async with render_sem:
                return await _run_scraper(s)
        return await _run_scraper(s)

    n_render = sum(1 for s in scrapers if getattr(s, "requires_render", False))
    print(
        f"运行 {len(scrapers)} 个抓取器(其中 {n_render} 个走渲染,"
        f"并发上限 {settings.render_concurrency})…"
    )
    results = await asyncio.gather(*(_guarded(s) for s in scrapers))
    return [row for group in results for row in group]


async def run_once(dry_run: bool = False) -> int:
    scraped = await collect()

    prev_snap = load_latest_snapshot()
    previous = prev_snap.entries if prev_snap else []

    if not scraped:
        print("⚠️  本轮抓取结果为空,保留上一快照,不覆盖。", file=sys.stderr)
        return 1

    entries, report = validate_and_merge(scraped, previous)
    overrides = load_overrides()
    entries = apply_overrides(entries, overrides)

    print(f"变更:{report.summary()} · override {len(overrides)} 条 · 合计 {len(entries)} 条")
    for tag, items in (("冻结", report.frozen), ("丢弃", report.dropped)):
        for lbl in items:
            print(f"  [{tag}] {lbl}", file=sys.stderr)

    if dry_run:
        print("dry-run:不写库/快照。")
        return 0

    path = write_snapshot(entries)
    n = upsert_entries(entries)
    print(f"✅ 快照写入 {path.name},入库 {n} 条。")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Model Price Hub 抓取管线")
    parser.add_argument("--dry-run", action="store_true", help="只抓取+校验,不写库/快照")
    args = parser.parse_args()
    rc = asyncio.run(run_once(dry_run=args.dry_run))
    sys.exit(rc)


if __name__ == "__main__":
    main()
