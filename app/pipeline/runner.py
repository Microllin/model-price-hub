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
import fcntl
import sys
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path

from app.config import settings
from app.db.session import sync_entries
from app.models.canonical import is_official
from app.models.pricing import RawPrice
from app.pipeline.health import update_status
from app.api.webhooks import build_events, deliver_events
from app.agents.tasks import enqueue
from app.pipeline.drift import build_drift_report, write_drift_report
from app.pipeline.store import load_latest_snapshot, load_overrides, write_snapshot
from app.pipeline.validate import apply_overrides, validate_and_merge
from app.scrapers.registry import all_scrapers


async def _run_scraper(scraper) -> tuple[list[RawPrice], bool]:
    name = scraper.__class__.__name__
    try:
        rows = await scraper.fetch()
        for r in rows:              # 标记数据源,供多源交叉验证
            r.source = scraper.source_name
        healthy = bool(rows)
        if not healthy and getattr(scraper, "channel", "") == "official" and not scraper.source_name.startswith("vision-"):
            enqueue(provider=getattr(scraper, "provider", "unknown"), source=scraper.source_name, source_url=getattr(scraper, "source_url", ""), reason="官方抓取返回空结果，需截图/OCR自动复核", agent="ocr")
        print(f"  [ok]   {name}: {len(rows)} 条" + ("（空结果，不参与下线判断）" if not healthy else ""))
        return rows, healthy
    except Exception as exc:  # 单个失败不影响整体
        if getattr(scraper, "channel", "") == "official":
            enqueue(provider=getattr(scraper, "provider", "unknown"), source=scraper.source_name, source_url=getattr(scraper, "source_url", ""), reason=f"官方抓取失败: {exc!r}", agent="ocr")
        print(f"  [fail] {name}: {exc!r}", file=sys.stderr)
        return [], False


async def collect() -> tuple[list[RawPrice], set[str]]:
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
    # 严格串行：慢一点，但不会同时启动多个 HTTP/Chromium/视觉任务。
    results: list[tuple[list[RawPrice], bool]] = []
    for scraper in scrapers:
        rows, healthy = await _guarded(scraper)
        if healthy:
            # source_name 与 runner 注入到 RawPrice 的 source 一致。
            results.append((rows, True))
        else:
            results.append(([], False))
    healthy_sources = {scrapers[i].source_name for i, (_, healthy) in enumerate(results) if healthy}
    return [row for group, _ in results for row in group], healthy_sources


@contextmanager
def _pipeline_lock():
    """跨 scheduler/updater 进程互斥，避免两次抓取叠加启动浏览器。"""
    lock_path = Path(settings.pipeline_health_path).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("已有另一条抓取管道运行中，跳过本次执行")
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


async def run_once(dry_run: bool = False) -> int:
    try:
        with _pipeline_lock():
            update_status(status="running", started_at=datetime.now(timezone.utc).isoformat(), error=None)
            scraped, healthy_sources = await collect()
            return await _finish_run(scraped, healthy_sources, dry_run)
    except RuntimeError as exc:
        update_status(status="skipped", error=str(exc))
        print(f"⚠️ {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        update_status(status="failed", error=repr(exc))
        raise


async def _finish_run(scraped: list[RawPrice], healthy_sources: set[str], dry_run: bool = False) -> int:

    prev_snap = load_latest_snapshot()
    previous = prev_snap.entries if prev_snap else []

    if not scraped:
        update_status(status="failed", error="all scrapers returned no data", scraped=0)
        print("⚠️  本轮抓取结果为空,保留上一快照,不覆盖。", file=sys.stderr)
        return 1

    # 官方源若条目数骤降，优先交给 Agent 截图/OCR 自动复核，避免页面局部加载被当成价格下线。
    effective_healthy = set(healthy_sources)
    approvals_path = settings.agent_tasks_path.with_name("agent-approvals.json")
    try:
        import json
        approvals = json.loads(approvals_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        approvals = {}
    for source in list(effective_healthy):
        old_rows = [e for e in previous if e.source == source and e.official]
        new_rows = [e for e in scraped if e.source == source and is_official(e.channel, e.source)]
        approved_count = approvals.get(source, {}).get("rendered_count")
        approved_structure = approvals.get(source, {}).get("structure_ok", True)
        if old_rows and len(new_rows) < max(1, len(old_rows) // 2) and (approved_count != len(new_rows) or not approved_structure):
            effective_healthy.discard(source)
            sample = old_rows[0]
            enqueue(provider=sample.provider, source=source, source_url=sample.source_url, reason=f"官方价格条目骤降: {len(old_rows)} → {len(new_rows)}，需截图/OCR自动复核", agent="ocr", payload={"old_count": len(old_rows), "new_count": len(new_rows)})
    entries, report = validate_and_merge(scraped, previous, healthy_sources=effective_healthy)
    overrides = load_overrides()
    entries = apply_overrides(entries, overrides)
    # 冻结条目可能复制旧快照中的 official 标记；每轮统一按 channel+source 重算。
    entries = [e.model_copy(update={"official": is_official(e.channel, e.source)}) for e in entries]

    print(f"变更:{report.summary()} · override {len(overrides)} 条 · 合计 {len(entries)} 条")
    for tag, items in (("冻结", report.frozen), ("丢弃", report.dropped)):
        for lbl in items:
            print(f"  [{tag}] {lbl}", file=sys.stderr)

    if dry_run:
        update_status(status="success", scraped=len(scraped), entries=len(entries), dry_run=True)
        print("dry-run:不写库/快照。")
        return 0

    path = write_snapshot(entries)
    n = sync_entries(entries)
    # 数据质量漂移报告：新增/消失模型、未匹配官方条目、孤儿模型、跨源价格偏差、维度覆盖率
    try:
        drift = build_drift_report(entries, previous)
        write_drift_report(drift)
        c = drift["counts"]
        print(f"🔍 漂移报告:新增 {c['models_new']} · 消失 {c['models_removed']} · 未匹配官方 {c['unmatched_official']} · 孤儿 {c['orphan_models']} · 价格偏差 {c['price_drift']}")
    except Exception as exc:  # 报告失败不影响入库
        print(f"⚠️ 漂移报告生成失败：{exc!r}", file=sys.stderr)
    events = build_events(previous, entries, scraped, path.stem, healthy_sources=effective_healthy)
    delivery = {"eligible": 0, "delivered": 0, "succeeded": 0, "failed": 0}
    try:
        delivery = deliver_events(events)
        if delivery["eligible"]:
            print(f"📣 Webhook 官方事件 {delivery['eligible']} 个，实际投递 {delivery['delivered']} 个，成功 {delivery['succeeded']}，失败 {delivery['failed']}。")
    except Exception as exc:
        # 通知失败不回滚已经成功写入的价格数据
        print(f"⚠️ Webhook 分发失败，不影响本轮入库：{exc!r}", file=sys.stderr)
    update_status(status="success", scraped=len(scraped), entries=len(entries), database_rows=n, webhook_events=delivery["delivered"], dry_run=False)
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
