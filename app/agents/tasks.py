"""Agent 自动复核任务队列。"""
from __future__ import annotations
import json, secrets
from datetime import datetime, timezone
from app.config import settings

def enqueue(*, provider: str, source: str, source_url: str, reason: str, agent: str = "ocr", payload: dict | None = None) -> dict:
    path=settings.agent_tasks_path; path.parent.mkdir(parents=True,exist_ok=True)
    try: tasks=json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError,json.JSONDecodeError): tasks=[]
    fingerprint=f"{provider}|{source}|{reason}|{datetime.now(timezone.utc).date().isoformat()}"
    if any(x.get("fingerprint")==fingerprint and x.get("status") in {"queued","running"} for x in tasks):
        return next(x for x in tasks if x.get("fingerprint")==fingerprint and x.get("status") in {"queued","running"})
    task={"id":secrets.token_urlsafe(10),"fingerprint":fingerprint,"provider":provider,"source":source,"source_url":source_url,"agent":agent,"reason":reason,"payload":payload or {},"status":"queued","created_at":datetime.now(timezone.utc).isoformat()}
    tasks.append(task); path.write_text(json.dumps(tasks,ensure_ascii=False,indent=2),encoding="utf-8"); return task
