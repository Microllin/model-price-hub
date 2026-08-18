"""抓取管线健康状态：使用小型 JSON 文件跨进程共享，不引入常驻数据库/监控服务。"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_status() -> dict[str, Any]:
    path = settings.pipeline_health_path
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "unknown", "updated_at": None}


def update_status(**values: Any) -> dict[str, Any]:
    """原子写入，避免 API 读到半截 JSON。"""
    path = settings.pipeline_health_path
    path.parent.mkdir(parents=True, exist_ok=True)
    current = read_status()
    current.update(values)
    current["updated_at"] = _now()
    fd, tmp = tempfile.mkstemp(prefix=".pipeline-health-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    return current
