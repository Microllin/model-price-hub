"""快照读写 + override 层加载。

- 快照:data/snapshots/<date>.json,同时更新 data/latest.json。入 git → 历史/审计/兜底。
- override:data/overrides.yaml,人工维护,provenance=manual,补抓不到的厂商或纠正抓取值。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.config import settings
from app.models.canonical import canonicalize, is_official
from app.models.pricing import (
    Currency,
    PriceEntry,
    Provenance,
    Region,
    Snapshot,
)


def load_latest_snapshot() -> Snapshot | None:
    """读取上一次快照(优先 latest.json)。无则 None。"""
    p = settings.latest_path
    if not p.exists():
        snaps = sorted(settings.snapshots_dir.glob("*.json"))
        if not snaps:
            return None
        p = snaps[-1]
    return Snapshot.model_validate_json(p.read_text(encoding="utf-8"))


def write_snapshot(entries: list[PriceEntry], data_date: str | None = None) -> Path:
    date = data_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snap = Snapshot(data_date=date, entries=entries)
    payload = snap.model_dump_json(indent=2)
    out = settings.snapshots_dir / f"{date}.json"
    out.write_text(payload, encoding="utf-8")
    settings.latest_path.write_text(payload, encoding="utf-8")
    return out


def load_overrides(path: Path | None = None) -> list[PriceEntry]:
    """加载人工 override 层。文件不存在则返回空。"""
    p = path or settings.overrides_path
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    entries: list[PriceEntry] = []
    for item in raw.get("entries", []):
        entries.append(
            PriceEntry(
                provider=item["provider"],
                channel=item.get("channel", "official"),
                model=item["model"],
                canonical_model=canonicalize(item["model"]),
                region=Region(item["region"]),
                currency=Currency(item["currency"]),
                official=is_official(item.get("channel", "official")),
                input_per_1m=item.get("input_per_1m"),
                output_per_1m=item.get("output_per_1m"),
                cached_input_per_1m=item.get("cached_input_per_1m"),
                cache_write_per_1m=item.get("cache_write_per_1m"),
                context_window=item.get("context_window"),
                max_output=item.get("max_output"),
                source_url=item.get("source_url", ""),
                source="override",
                provenance=Provenance.MANUAL,
            )
        )
    return entries
