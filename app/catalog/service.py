"""第一层：官方模型目录快照与生命周期差异。独立于价格和 Discovery。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from app.config import settings

ROOT = Path(__file__).resolve().parent
DB = settings.catalog_db_path
SCHEMA = """
CREATE TABLE IF NOT EXISTS catalogs (provider TEXT NOT NULL, source_id TEXT NOT NULL, model TEXT NOT NULL, title TEXT, source_url TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(provider,source_id,model));
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT, sources INTEGER, models INTEGER, added INTEGER, removed INTEGER, error TEXT);
"""


def _now() -> str: return datetime.now(timezone.utc).isoformat()

def _db():
    DB.parent.mkdir(parents=True, exist_ok=True); c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.executescript(SCHEMA); return c

def _config():
    p=settings.source_config_path
    try: return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError,json.JSONDecodeError):
        from app.api.sources import DEFAULTS
        return list(DEFAULTS)

def _model_ids(text: str, url: str) -> set[str]:
    clean=re.sub(r"<[^>]+>", " ", text)
    corpus=clean+" "+url
    patterns=(r"\b(?:gpt|o[134]|claude|gemini|deepseek|glm|qwen|kimi|grok|minimax|ernie|hunyuan)[a-z0-9._:-]*",)
    found=set()
    for p in patterns: found.update(x.lower().strip(".:,") for x in re.findall(p,corpus,re.I))
    # 过滤普通导航词，保留明显模型 ID；页面自身 URL 也可作为单模型证据。
    blocked = {"models", "model", "modelid", "model-intro", "deepseek", "gemini", "claude", "grok", "qwen", "kimi", "minimax", "ernie", "hunyuan", "deepseek.com", "gpts"}
    return {x for x in found if len(x)>3 and x not in blocked}

def run() -> dict:
    started=_now(); c=_db(); added=removed=models=sources=0; events=[]
    baseline = c.execute("SELECT 1 FROM runs LIMIT 1").fetchone() is None
    try:
        for source in _config():
            if not source.get("enabled",True) or source.get("kind")!="model_catalog": continue
            sources+=1; sid=source["id"]
            try:
                with httpx.Client(timeout=settings.http_timeout,follow_redirects=True,**({"proxy": settings.http_proxy} if settings.http_proxy else {}),headers={"User-Agent":settings.user_agent}) as client:
                    r=client.get(source["url"]); r.raise_for_status(); text=r.text
                current=_model_ids(text,source["url"]); models+=len(current); now=_now()
                old={x["model"]:x for x in c.execute("SELECT * FROM catalogs WHERE provider=? AND source_id=? AND active=1",(source["provider"],sid))}
                for model in sorted(current):
                    if model not in old:
                        c.execute("INSERT OR REPLACE INTO catalogs VALUES(?,?,?,?,?,?,?,1)",(source["provider"],sid,model,model,source["url"],now,now)); added+=1
                        if not baseline: events.append({"event":"model_added","occurred_at":now,"provider":source["provider"],"model":model,"source":"official-page","source_url":source["url"]})
                    else: c.execute("UPDATE catalogs SET last_seen=?,active=1 WHERE provider=? AND source_id=? AND model=?",(now,source["provider"],sid,model))
                for model,row in old.items():
                    if model not in current:
                        c.execute("UPDATE catalogs SET active=0,last_seen=? WHERE provider=? AND source_id=? AND model=?",(now,source["provider"],sid,model)); removed+=1
                        if not baseline: events.append({"event":"model_removed","occurred_at":now,"provider":source["provider"],"model":model,"source":"official-page","source_url":source["url"]})
            except Exception as exc:
                c.execute("INSERT INTO runs(started_at,finished_at,status,sources,models,added,removed,error) VALUES(?,?,?,?,?,?,?,?)",(started,_now(),"failed",sources,models,added,removed,repr(exc))); c.commit(); continue
        c.execute("INSERT INTO runs(started_at,finished_at,status,sources,models,added,removed,error) VALUES(?,?,?,?,?,?,?,?)",(started,_now(),"success",sources,models,added,removed,None)); c.commit(); return {"status":"success","sources":sources,"models":models,"added":added,"removed":removed,"events":events}
    finally: c.close()

def list_models(provider: str|None=None):
    c=_db(); q="SELECT * FROM catalogs WHERE active=1"; args=[]
    if provider: q+=" AND provider=?"; args.append(provider)
    rows=[dict(x) for x in c.execute(q,args)]; c.close(); return rows


def health() -> dict:
    c=_db(); row=c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone(); count=c.execute("SELECT count(*) FROM catalogs WHERE active=1").fetchone()[0]; c.close()
    return {"status": row["status"] if row else "never_run", "active_models": count, "last_run": dict(row) if row else None}
