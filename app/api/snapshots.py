"""历史快照查询路由。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.models.pricing import Snapshot

router = APIRouter(prefix="/v1/snapshots", tags=["snapshots"])


@router.get("")
def list_snapshots():
    dates = sorted(p.stem for p in settings.snapshots_dir.glob("*.json"))
    return {"count": len(dates), "dates": dates}


@router.get("/{date}")
def get_snapshot(date: str):
    p = settings.snapshots_dir / f"{date}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"无快照 {date}")
    snap = Snapshot.model_validate_json(p.read_text(encoding="utf-8"))
    return snap
