"""运行策略：后台可配置的行为开关，被价格管线与 Webhook 分发实时消费。

策略存储在 data/policy.json；未配置时使用 DEFAULT_POLICY。
管理员在后台修改后立即生效，无需重启服务。
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings


class Policy(BaseModel):
    """平台运行策略。字段说明见后台「策略配置」页。"""

    # 单项价格相较上一快照变动超过该比例 → 冻结(保留旧值并标记 needs_review)
    price_change_freeze_ratio: float = Field(default=0.40, ge=0, le=10)
    # 官方结构化抓取成功时自动应用；关闭后官方变更也进入人工/Agent 复核
    official_auto_apply: bool = True
    # 官方页面异常时由 Agent 自动截图/OCR 复核
    agent_auto_review: bool = True
    # 模型上线/下线事件投递 Webhook
    notify_model_lifecycle: bool = True
    # 官方价格变化事件投递 Webhook
    notify_price_changes: bool = True
    # 放宽 Webhook 官方事件过滤,允许第三方旁证价格事件投递
    notify_third_party: bool = False
    # Discovery 监控调度间隔(小时)
    discovery_interval_hours: int = Field(default=6, ge=1, le=168)
    # 官方模型目录调度间隔(小时)
    catalog_interval_hours: int = Field(default=6, ge=1, le=168)
    # 开启后,只读查询 API(/v1/prices 等)必须携带有效 X-API-Key
    api_key_required: bool = False


DEFAULT_POLICY: dict[str, Any] = Policy().model_dump()


def _path():
    return settings.data_dir / "policy.json"


def get_policy() -> Policy:
    """读取当前策略；文件缺失或损坏时回退默认值。"""
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        raw = {}
    return Policy(**{**DEFAULT_POLICY, **raw})


def save_policy(policy: Policy) -> Policy:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(policy.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(p)
    return policy
