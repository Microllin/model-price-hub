"""官方模型目录调度器：每6小时运行一次。"""
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.catalog.service import run
from app.api.webhooks import deliver_events

def cycle():
    result=run(); events=[]
    for e in result.get("events",[]):
        events.append({"event":e["event"],"occurred_at":e["occurred_at"],"data_date":e["occurred_at"][:10],"model":{"provider":e["provider"],"model":e["model"],"canonical_model":e["model"],"channel":"official","region":"intl","currency":"USD","service_tier":"standard","modality":"text","billing_unit":"token","source":"official-page","source_url":e["source_url"]}})
    print({**{k:v for k,v in result.items() if k!='events'},"webhook":deliver_events(events)},flush=True)

def main():
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop); s=AsyncIOScheduler(event_loop=loop); s.add_job(lambda:asyncio.to_thread(cycle),'interval',hours=6,id='catalog',max_instances=1,coalesce=True); s.start(); print('Catalog scheduler started: every 6h',flush=True); loop.run_forever()
if __name__=='__main__': main()
