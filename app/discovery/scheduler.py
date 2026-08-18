"""Discovery 独立调度器：不触发价格抓取，每 6 小时采集一次。"""
from __future__ import annotations

import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.api.webhooks import deliver_events
from app.discovery.service import apply_verified_additions, apply_verified_removals, run


def run_cycle() -> None:
    result = run()
    additions = apply_verified_additions(result["started_at"])
    removals = apply_verified_removals()
    delivery = deliver_events(additions["events"] + removals["events"])
    print(f"Discovery 完成：新增上线 {additions['applied_models']}，官方下线 {removals['applied_models']}，Webhook {delivery}", flush=True)


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    scheduler = AsyncIOScheduler(event_loop=loop)
    scheduler.add_job(lambda: asyncio.to_thread(run_cycle), "interval", hours=6, id="discovery", max_instances=1, coalesce=True, misfire_grace_time=3600)
    scheduler.start()
    print("Discovery 调度器已启动：每 6 小时运行一次。", flush=True)
    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
