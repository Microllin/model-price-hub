"""官方来源与 Agent 任务配置。"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, HttpUrl

from app.api.admin import require_admin
from app.config import settings

router = APIRouter(prefix="/v1/sources", tags=["sources"])
SourceKind = Literal["model_catalog", "pricing", "monitor", "rss", "announcement"]
AgentKind = Literal["none", "html_extract", "ocr", "page_diff", "context_review"]

DEFAULTS = [
    {"id":"openai-models", "provider":"openai", "name":"OpenAI 官方模型目录", "kind":"model_catalog", "url":"https://developers.openai.com/api/docs/models", "domain":"developers.openai.com", "agent":"html_extract", "enabled":True},
    {"id":"openai-pricing", "provider":"openai", "name":"OpenAI 官方价格页", "kind":"pricing", "url":"https://developers.openai.com/api/docs/pricing", "domain":"developers.openai.com", "agent":"html_extract", "enabled":True},
    {"id":"anthropic-models", "provider":"anthropic", "name":"Anthropic Claude 官方模型文档", "kind":"model_catalog", "url":"https://docs.anthropic.com/en/docs/about-claude/models", "domain":"docs.anthropic.com", "agent":"html_extract", "enabled":True},
    {"id":"anthropic-pricing", "provider":"anthropic", "name":"Anthropic 官方价格页", "kind":"pricing", "url":"https://docs.anthropic.com/en/docs/about-claude/pricing", "domain":"docs.anthropic.com", "agent":"html_extract", "enabled":True},
    {"id":"google-models", "provider":"google", "name":"Google Gemini 官方模型页", "kind":"model_catalog", "url":"https://ai.google.dev/gemini-api/docs/models", "domain":"ai.google.dev", "agent":"html_extract", "enabled":True},
    {"id":"google-pricing", "provider":"google", "name":"Google Gemini 官方价格页", "kind":"pricing", "url":"https://ai.google.dev/gemini-api/docs/pricing", "domain":"ai.google.dev", "agent":"html_extract", "enabled":True},
    {"id":"xai-models", "provider":"xai", "name":"xAI 官方模型目录", "kind":"model_catalog", "url":"https://docs.x.ai/developers/models", "domain":"docs.x.ai", "agent":"html_extract", "enabled":True},
    {"id":"xai-pricing", "provider":"xai", "name":"xAI 官方价格页", "kind":"pricing", "url":"https://docs.x.ai/developers/pricing", "domain":"docs.x.ai", "agent":"html_extract", "enabled":True},
    {"id":"qwen-models", "provider":"qwen", "name":"通义千问官方模型页", "kind":"model_catalog", "url":"https://help.aliyun.com/zh/model-studio/models", "domain":"help.aliyun.com", "agent":"html_extract", "enabled":True},
    {"id":"qwen-pricing", "provider":"qwen", "name":"通义千问官方价格页", "kind":"pricing", "url":"https://help.aliyun.com/zh/model-studio/model-pricing", "domain":"help.aliyun.com", "agent":"html_extract", "enabled":True},
    {"id":"deepseek-models-cn", "provider":"deepseek", "name":"DeepSeek 官方模型/价格页（中文）", "kind":"model_catalog", "url":"https://api-docs.deepseek.com/zh-cn/quick_start/pricing", "domain":"api-docs.deepseek.com", "agent":"html_extract", "enabled":True},
    {"id":"zhipu-models", "provider":"zhipu", "name":"智谱官方模型总览", "kind":"model_catalog", "url":"https://docs.bigmodel.cn/cn/guide/start/model-overview", "domain":"docs.bigmodel.cn", "agent":"html_extract", "enabled":True},
    {"id":"minimax-models", "provider":"minimax", "name":"MiniMax 官方模型介绍", "kind":"model_catalog", "url":"https://platform.minimaxi.com/docs/guides/models-intro", "domain":"platform.minimaxi.com", "agent":"html_extract", "enabled":True},
    {"id":"deepseek-pricing-cn", "provider":"deepseek", "name":"DeepSeek 官方价格页", "kind":"pricing", "url":"https://api-docs.deepseek.com/zh-cn/quick_start/pricing", "domain":"api-docs.deepseek.com", "agent":"html_extract", "enabled":True},
]


def _path() -> Path:
    return settings.source_config_path


def _read() -> list[dict]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        _write(DEFAULTS)
        return list(DEFAULTS)


def _write(items: list[dict]) -> None:
    p = _path(); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


class SourceInput(BaseModel):
    provider: str
    name: str
    kind: SourceKind
    url: HttpUrl
    domain: str = ""
    agent: AgentKind = "html_extract"
    enabled: bool = True
    schedule_minutes: int = Field(default=360, ge=5, le=10080)


class AgentRunInput(BaseModel):
    agent: AgentKind = "context_review"
    instruction: str = "请解析页面中的模型 ID、上下线状态、价格条件和生效时间，并返回证据。"


@router.get("")
def list_sources(admin: dict = Depends(require_admin)):
    return {"sources": _read(), "kinds": ["model_catalog", "pricing", "monitor", "rss", "announcement"], "agents": ["none", "html_extract", "ocr", "page_diff", "context_review"]}


@router.post("")
def create_source(body: SourceInput, admin: dict = Depends(require_admin)):
    items = _read()
    item = {"id": secrets.token_urlsafe(9), **body.model_dump(mode="json"), "created_at": datetime.now(timezone.utc).isoformat()}
    items.append(item); _write(items)
    return item


@router.post("/tasks/run")
async def run_agent_tasks(admin: dict = Depends(require_admin)):
    from app.agents.worker import process_once
    return await process_once()


@router.get("/tasks")
def list_agent_tasks(admin: dict = Depends(require_admin)):
    p = settings.agent_tasks_path
    return {"tasks": json.loads(p.read_text(encoding="utf-8")) if p.exists() else []}


@router.put("/{source_id}")
def update_source(source_id: str, body: SourceInput, admin: dict = Depends(require_admin)):
    items = _read()
    for i, old in enumerate(items):
        if old["id"] == source_id:
            item = {**old, **body.model_dump(mode="json"), "updated_at": datetime.now(timezone.utc).isoformat()}
            items[i] = item; _write(items); return item
    raise HTTPException(404, "来源不存在")


@router.delete("/{source_id}")
def delete_source(source_id: str, admin: dict = Depends(require_admin)):
    items = _read(); remaining = [x for x in items if x["id"] != source_id]
    if len(items) == len(remaining): raise HTTPException(404, "来源不存在")
    _write(remaining); return {"ok": True}


@router.post("/{source_id}/agent")
def run_agent(source_id: str, body: AgentRunInput | None = None, admin: dict = Depends(require_admin)):
    source = next((x for x in _read() if x["id"] == source_id), None)
    if source is None: raise HTTPException(404, "来源不存在")
    # 任务入口先落地；真正 OCR/上下文模型由 Agent worker 消费，避免 API 进程阻塞。
    task = {"id": secrets.token_urlsafe(10), "source_id": source_id, "agent": (body.agent if body else source.get("agent", "context_review")), "instruction": (body.instruction if body else "请解析模型与价格变更"), "status": "queued", "created_at": datetime.now(timezone.utc).isoformat()}
    p = settings.agent_tasks_path; p.parent.mkdir(parents=True, exist_ok=True)
    tasks = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    tasks.append(task); p.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    return task

