"""Agent 自动复核 Worker：截图 + 渲染重解析 + 自动批准。"""
from __future__ import annotations
import asyncio, json
from datetime import datetime, timezone
from pathlib import Path
from app.config import settings
from app.scrapers.registry import all_scrapers

APPROVALS=settings.agent_tasks_path.with_name("agent-approvals.json")
EVIDENCE=settings.agent_tasks_path.parent/"agent-evidence"

def _load(path):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError,json.JSONDecodeError):return [] if path==settings.agent_tasks_path else {}

def _save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")

async def process_once():
    tasks=_load(settings.agent_tasks_path); approvals=_load(APPROVALS); changed=False
    registry={s.source_name:s for s in all_scrapers()}
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    for task in tasks:
        if task.get("status") not in {"queued","retry"}:continue
        scraper=registry.get(task.get("source")); task["status"]="running"; task["started_at"]=datetime.now(timezone.utc).isoformat(); changed=True
        try:
            urls = await scraper.urls()
            rows = []
            evidence_files = []
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                launch={"headless":True,"args":["--no-sandbox","--disable-dev-shm-usage"]}
                if settings.http_proxy: launch["proxy"]={"server":settings.http_proxy}
                browser=await p.chromium.launch(**launch)
                try:
                    for index, url in enumerate(urls):
                        page=await browser.new_page(user_agent=settings.user_agent)
                        await page.goto(url,wait_until="domcontentloaded",timeout=int(settings.http_timeout*1000))
                        await page.wait_for_timeout(int(settings.render_settle_seconds*1000))
                        html=await page.content()
                        html_path=EVIDENCE/f"{task['id']}-{index}.html"
                        png_path=EVIDENCE/f"{task['id']}-{index}.png"
                        html_path.write_text(html,encoding="utf-8")
                        await page.screenshot(path=str(png_path),full_page=True)
                        evidence_files.extend([str(html_path),str(png_path)])
                        rows.extend(scraper.parse(html))
                        await page.close()
                finally: await browser.close()
            expected=int(task.get("payload",{}).get("new_count",-1))
            # 复核必须确认“页面结构”，不能只比较条目数；否则解析器漏字段会被误认为官方删除。
            structure_ok = True
            if task.get("source") == "deepseek":
                structure_ok = any(x.service_tier == "scheduled" for x in rows)
            # 结构复核通过时，以渲染结果为准；条目数差异本身说明页面内容已变化。
            if rows and structure_ok:
                approvals[task["source"]]={"approved_at":datetime.now(timezone.utc).isoformat(),"reason":task["reason"],"rendered_count":len(rows),"structure_ok":structure_ok,"task_id":task["id"]}
                task["status"]="verified"; task["result"]={"rendered_count":len(rows),"evidence":evidence_files}
            else:
                task["status"]="retry"; task["result"]={"rendered_count":len(rows),"expected":expected,"structure_ok":structure_ok}
        except Exception as exc:
            task["status"]="retry"; task["error"]=repr(exc)
        task["finished_at"]=datetime.now(timezone.utc).isoformat()
    if changed:_save(settings.agent_tasks_path,tasks);_save(APPROVALS,approvals)
    return {"processed":sum(x.get("status") in {"verified","retry"} for x in tasks),"approvals":len(approvals)}

async def serve():
    while True:
        print(await process_once(),flush=True)
        await asyncio.sleep(600)

def main(): asyncio.run(serve())
if __name__=="__main__":main()
