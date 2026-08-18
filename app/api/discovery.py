"""模型变更发现层：保存新闻/公告线索，等待验证后再执行。"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl

from app.api.admin import require_admin
from app.config import settings
from app.api.webhooks import deliver_events
from app.discovery.service import _db, apply_verified_additions, apply_verified_removals, run as run_discovery

router = APIRouter(prefix="/v1/discovery", tags=["discovery"])

ChangeType = Literal["model_added", "model_removed", "price_changed", "model_updated", "news"]
CandidateStatus = Literal["candidate", "verified", "rejected", "applied"]


class DiscoveryCandidate(BaseModel):
    id: str = Field(default_factory=lambda: secrets.token_urlsafe(10))
    provider: str
    model: str | None = None
    canonical_model: str | None = None
    change_type: ChangeType = "news"
    status: CandidateStatus = "candidate"
    confidence: Literal["low", "medium", "high"] = "low"
    title: str
    summary: str
    source_url: HttpUrl
    source_domain: str = ""
    published_at: datetime | None = None
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    keywords: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    proposed_change: dict[str, Any] = Field(default_factory=dict)
    reviewed_at: datetime | None = None
    review_note: str | None = None


class DiscoveryInput(BaseModel):
    provider: str
    model: str | None = None
    canonical_model: str | None = None
    change_type: ChangeType = "news"
    confidence: Literal["low", "medium", "high"] = "low"
    title: str
    summary: str
    source_url: HttpUrl
    source_domain: str = ""
    published_at: datetime | None = None
    keywords: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    proposed_change: dict[str, Any] = Field(default_factory=dict)


class ReviewInput(BaseModel):
    note: str | None = None


def _path() -> Path:
    return settings.discovery_candidates_path


def _read() -> list[DiscoveryCandidate]:
    try:
        return [DiscoveryCandidate.model_validate(x) for x in json.loads(_path().read_text(encoding="utf-8"))]
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return []


def _write(items: list[DiscoveryCandidate]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([x.model_dump(mode="json") for x in items], ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _fingerprint(item: DiscoveryInput) -> str:
    raw = f"{item.provider}|{item.model}|{item.change_type}|{item.source_url}"
    return hashlib.sha256(raw.encode()).hexdigest()


@router.post("/run")
def run_discovery_now(admin: dict = Depends(require_admin)):
    result = run_discovery()
    additions = apply_verified_additions(result["started_at"])
    removals = apply_verified_removals()
    delivery = deliver_events(additions["events"] + removals["events"])
    result["applied_addition_models"] = additions["applied_models"]
    result["applied_removal_models"] = removals["applied_models"]
    result["removed_price_rows"] = removals["removed"]
    result["webhook"] = delivery
    return result


@router.get("/runs")
def list_runs(limit: int = Query(20, ge=1, le=100)):
    conn = _db()
    rows = [dict(x) for x in conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    conn.close()
    return {"runs": rows}


@router.get("")
def list_candidates(
    status: CandidateStatus | None = None,
    provider: str | None = None,
    change_type: ChangeType | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    conn = _db()
    clauses, args = [], []
    if status: clauses.append("status=?"); args.append(status)
    if provider: clauses.append("provider=?"); args.append(provider)
    if change_type: clauses.append("change_type=?"); args.append(change_type)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(f"SELECT * FROM candidates{where} ORDER BY detected_at DESC LIMIT ?", (*args, limit)).fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["source_tier_label"] = {1: "官方单模型页", 2: "官方价格页", 3: "搜索/RSS/其他"}.get(item.get("source_tier", 3), "其他")
        for field in ("keywords", "evidence", "proposed_change"): 
            try: item[field] = json.loads(item[field] or "{}" if field == "proposed_change" else item[field] or "[]")
            except (TypeError, json.JSONDecodeError): item[field] = [] if field != "proposed_change" else {}
        result.append(item)
    return {"count": len(result), "candidates": result}


@router.post("")
def ingest_candidate(body: DiscoveryInput, admin: dict = Depends(require_admin)):
    items = _read()
    fp = _fingerprint(body)
    for old in items:
        if _fingerprint(DiscoveryInput(**old.model_dump(exclude={"id", "status", "detected_at", "reviewed_at", "review_note"}))) == fp:
            return old.model_dump(mode="json")
    item = DiscoveryCandidate(**body.model_dump())
    items.append(item)
    _write(items)
    return item.model_dump(mode="json")


@router.post("/{candidate_id}/verify")
def verify_candidate(candidate_id: str, body: ReviewInput | None = None, admin: dict = Depends(require_admin)):
    return _review(candidate_id, "verified", body.note if body else None)


@router.post("/{candidate_id}/reject")
def reject_candidate(candidate_id: str, body: ReviewInput | None = None, admin: dict = Depends(require_admin)):
    return _review(candidate_id, "rejected", body.note if body else None)


def _review(candidate_id: str, status: CandidateStatus, note: str | None):
    conn = _db()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute("UPDATE candidates SET status=?, reviewed_at=?, review_note=? WHERE id=?", (status, now, note, candidate_id))
    if not cur.rowcount:
        conn.close(); raise HTTPException(404, "变更候选不存在")
    row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    conn.commit(); conn.close()
    result = dict(row)
    result["source_tier_label"] = {1: "官方单模型页", 2: "官方价格页", 3: "搜索/RSS/其他"}.get(result.get("source_tier", 3), "其他")
    for field in ("keywords", "evidence", "proposed_change"):
        try: result[field] = json.loads(result[field] or ("{}" if field == "proposed_change" else "[]"))
        except (TypeError, json.JSONDecodeError): result[field] = {} if field == "proposed_change" else []
    return result
