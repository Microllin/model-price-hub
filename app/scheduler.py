"""容器内调度:APScheduler 每隔 N 天跑一次抓取管线(默认 3 天)。

视觉入库较慢,不必每天跑。间隔天数由 MPH_SCHEDULE_INTERVAL_DAYS 控制,首次触发时刻
由 MPH_SCHEDULE_HOUR/MINUTE 控制。也可不用此进程,改用 cron / k8s CronJob / GitHub
Action 调 `python -m app.pipeline.runner`。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.pipeline.runner import run_once


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    scheduler = AsyncIOScheduler(event_loop=loop)

    days = max(1, settings.schedule_interval_days)
    # 首次触发对齐到下一个 HH:MM(本地),之后每隔 N 天
    now = datetime.now()
    start = now.replace(
        hour=settings.schedule_hour, minute=settings.schedule_minute, second=0, microsecond=0
    )
    if start <= now:
        start += timedelta(days=1)

    scheduler.add_job(
        run_once,
        IntervalTrigger(days=days, start_date=start),
        id="price-update",
        misfire_grace_time=6 * 3600,  # 视觉跑得慢,宽限 6 小时
    )
    print(
        f"调度已启动:每 {days} 天更新一次价格,首次于 {start:%Y-%m-%d %H:%M},"
        f"之后每 {days} 天。立即先跑一次…"
    )
    loop.run_until_complete(run_once())
    scheduler.start()
    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
