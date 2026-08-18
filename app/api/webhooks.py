"""Webhook 配置与价格变更通知。"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.policy import get_policy
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, HttpUrl

from app.api.admin import require_admin
from app.config import settings
from app.models.canonical import canonicalize
from app.models.pricing import PriceEntry, RawPrice

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

EVENTS = {"price_changed", "model_added", "model_removed"}
# 只有厂商官方抓取器/人工 override 才能触发飞书，LiteLLM 不能冒充官方源。
OFFICIAL_SOURCES = {"aliyun", "baidu", "deepseek", "minimax", "tencent", "zhipu", "openai", "anthropic", "google", "override", "official-page", "vision-aliyun", "vision-baidu", "vision-deepseek", "vision-minimax", "vision-moonshot", "vision-tencent", "vision-zhipu"}


def _model_key(e: PriceEntry | RawPrice) -> tuple:
    model = getattr(e, "canonical_model", "") or canonicalize(e.model)
    return (e.provider, model, e.region.value, e.currency.value, e.service_tier, e.modality, e.billing_unit, e.cache_state or "", e.context_range or "", json.dumps(e.time_window, sort_keys=True) if e.time_window else "")


def _official_map(items: list[PriceEntry | RawPrice]) -> dict[tuple, PriceEntry | RawPrice]:
    result = {}
    for item in items:
        if getattr(item, "official", item.channel == "official") and getattr(item, "source", "") in OFFICIAL_SOURCES:
            result.setdefault(_model_key(item), item)
    return result


def _source_state() -> dict[str, Any]:
    try:
        return json.loads(settings.webhook_removal_state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_source_state(value: dict[str, Any]) -> None:
    path = settings.webhook_removal_state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _is_future_price(item: PriceEntry | RawPrice) -> bool:
    effective = getattr(item, "effective_from", None)
    return bool(effective and effective > datetime.now(timezone.utc))


def build_events(previous: list[PriceEntry], current: list[PriceEntry], scraped: list[RawPrice], data_date: str, healthy_sources: set[str] | None = None) -> list[dict[str, Any]]:
    """只比较当前生效价格；未来生效的调度价只能作为 price_changed，绝不能当模型上线/下线。"""
    old = _official_map(previous)
    now = _official_map(current)
    seen = _official_map(scraped)
    events: list[dict[str, Any]] = []
    occurred_at = datetime.now(timezone.utc).isoformat()
    for key, item in seen.items():
        marker = _state_key(key)
        prior_state = _source_state().get(marker, {})
        # 未来生效价先入库但不报警；否则会被误报为即时价格变化。
        if _is_future_price(item):
            continue
        if key not in old:
            # 价格层只记录价格变化；新模型必须由官方目录层产生 model_added。
            logical_old = next((old_key for old_key, old_item in old.items() if old_item.provider == item.provider and (getattr(old_item, "canonical_model", "") or old_item.model) == (getattr(item, "canonical_model", "") or item.model) and old_item.region == item.region and old_item.currency == item.currency), None)
            before = _event_item(old[logical_old]) if logical_old is not None else {**_event_item(item), "input_per_1m": None, "output_per_1m": None, "cached_input_per_1m": None, "cache_write_per_1m": None}
            events.append({"event": "price_changed", "occurred_at": occurred_at, "data_date": data_date, "change_action": "price_added", "before": before, "after": _event_item(item)})
        elif key in now and _price_changed(old[key], now[key]):
            events.append({"event": "price_changed", "occurred_at": occurred_at, "data_date": data_date, "before": _event_item(old[key]), "after": _event_item(now[key])})

    # 健康官方源中价格条件消失：自动执行价格下线，只产生 price_changed。
    healthy = healthy_sources or set()
    for key, item in old.items():
        if key in now or item.source not in healthy or _is_future_price(item):
            continue
        after = {**_event_item(item), "input_per_1m": None, "output_per_1m": None, "cached_input_per_1m": None, "cache_write_per_1m": None}
        events.append({"event": "price_changed", "occurred_at": occurred_at, "data_date": data_date, "change_action": "price_removed", "before": _event_item(item), "after": after})
    # 模型生命周期仍只由 catalog 服务判断。
    return events


def _state_key(key: tuple) -> str:
    return "|".join(key)


def _price_changed(a: PriceEntry | RawPrice, b: PriceEntry | RawPrice) -> bool:
    return any(getattr(a, field) != getattr(b, field) for field in ("input_per_1m", "output_per_1m", "cached_input_per_1m", "cache_write_per_1m", "service_tier", "time_window", "effective_from", "effective_to"))


def _event_item(item: PriceEntry | RawPrice) -> dict[str, Any]:
    return {"provider": item.provider, "model": item.model, "canonical_model": getattr(item, "canonical_model", "") or item.model, "channel": item.channel, "region": item.region.value, "currency": item.currency.value, "service_tier": item.service_tier, "modality": item.modality, "billing_unit": item.billing_unit, "cache_state": item.cache_state, "context_range": item.context_range, "time_window": item.time_window, "effective_from": item.effective_from.isoformat() if item.effective_from else None, "effective_to": item.effective_to.isoformat() if item.effective_to else None, "input_per_1m": item.input_per_1m, "output_per_1m": item.output_per_1m, "cached_input_per_1m": item.cached_input_per_1m, "cache_write_per_1m": item.cache_write_per_1m, "source": item.source, "source_url": item.source_url}


class WebhookConfig(BaseModel):
    id: str = Field(default_factory=lambda: secrets.token_urlsafe(9))
    name: str = "价格变更通知"
    url: HttpUrl
    enabled: bool = True
    secret: str | None = None
    events: list[str] = Field(default_factory=lambda: sorted(EVENTS))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_delivery: dict[str, Any] | None = None


class WebhookInput(BaseModel):
    name: str = "价格变更通知"
    url: HttpUrl
    enabled: bool = True
    secret: str | None = None
    events: list[str] = Field(default_factory=lambda: sorted(EVENTS))


def _path() -> Path:
    return settings.webhook_config_path


def _read() -> list[WebhookConfig]:
    try:
        return [WebhookConfig.model_validate(x) for x in json.loads(_path().read_text())]
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return []


def _write(items: list[WebhookConfig]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([x.model_dump(mode="json") for x in items], ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _public(item: WebhookConfig) -> dict[str, Any]:
    value = item.model_dump(mode="json")
    value["url"] = str(item.url)
    value["has_secret"] = bool(item.secret)
    value["secret"] = None
    return value


def _validate_events(events: list[str]) -> None:
    if not events or any(x not in EVENTS for x in events):
        raise HTTPException(400, f"events 只能包含: {', '.join(sorted(EVENTS))}")


@router.get("")
def list_webhooks(admin: dict = Depends(require_admin)):
    return {"webhooks": [_public(x) for x in _read()]}


@router.post("")
def create_webhook(body: WebhookInput, admin: dict = Depends(require_admin)):
    _validate_events(body.events)
    items = _read()
    item = WebhookConfig(**body.model_dump())
    items.append(item)
    _write(items)
    return _public(item)


@router.put("/{webhook_id}")
def update_webhook(webhook_id: str, body: WebhookInput, admin: dict = Depends(require_admin)):
    _validate_events(body.events)
    items = _read()
    for i, old in enumerate(items):
        if old.id == webhook_id:
            secret = body.secret if body.secret is not None else old.secret
            item = WebhookConfig(id=old.id, created_at=old.created_at, last_delivery=old.last_delivery, secret=secret, **body.model_dump(exclude={"secret"}))
            items[i] = item
            _write(items)
            return _public(item)
    raise HTTPException(404, "Webhook 不存在")


@router.delete("/{webhook_id}")
def delete_webhook(webhook_id: str, admin: dict = Depends(require_admin)):
    items = _read()
    remaining = [x for x in items if x.id != webhook_id]
    if len(remaining) == len(items):
        raise HTTPException(404, "Webhook 不存在")
    _write(remaining)
    return {"ok": True}


def _headers(payload: bytes, secret: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "User-Agent": "ModelPriceHub-Webhook/1.0", "X-MPH-Event": "price_update"}
    if secret:
        digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        headers["X-MPH-Signature"] = f"sha256={digest}"
    return headers


def _is_feishu(item: WebhookConfig) -> bool:
    return "open.feishu.cn/open-apis/bot/" in str(item.url)


def _price_value(value: Any) -> str:
    if value is None:
        return "—"
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def _card_field(label: str, value: Any) -> str:
    return f"**{label}**  {value if value not in (None, '') else '—'}"


def _feishu_card(event: dict[str, Any]) -> dict[str, Any]:
    event_name = event.get("event", "notification")
    meta = {
        "test": ("连接测试成功", "green", "Webhook 已成功接入 Model Price Hub"),
        "price_changed": ("官方价格发生变动", "orange", "检测到官方价格条件发生变化"),
        "model_added": ("官方模型已上线", "green", "官方数据源发现新的可用模型"),
        "model_removed": ("官方模型已下线", "red", "官方模型下线已通过监控规则确认"),
    }
    title, color, summary = meta.get(event_name, ("模型价格监控通知", "blue", "Model Price Hub 事件通知"))
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": f"**{summary}**"}, {"tag": "hr"}]

    if event_name == "test":
        elements.append({"tag": "markdown", "content": "✅ **Webhook 配置测试成功**\n\n飞书卡片消息通道、网络连接和消息格式均正常。"})
    elif event_name == "price_changed":
        before, after = event.get("before", {}), event.get("after", {})
        details = [
            _card_field("厂商", after.get("provider")),
            _card_field("模型", f"`{after.get('model', '—')}`"),
            _card_field("服务档位", after.get("service_tier", "standard")),
            _card_field("模态 / 计费", f"{after.get('modality', 'text')} / {after.get('billing_unit', 'token')}"),
            _card_field("地区 / 币种", f"{after.get('region', '—')} / {after.get('currency', '—')}"),
            _card_field("输入价格", f"{_price_value(before.get('input_per_1m'))} → **{_price_value(after.get('input_per_1m'))}** / 1M"),
            _card_field("输出价格", f"{_price_value(before.get('output_per_1m'))} → **{_price_value(after.get('output_per_1m'))}** / 1M"),
        ]
        if after.get("cache_state"):
            details.append(_card_field("缓存状态", after["cache_state"]))
        if after.get("time_window"):
            tw = after["time_window"]
            details.append(_card_field("生效时段", f"{tw.get('timezone', '')} {tw.get('start', '')}–{tw.get('end', '')}"))
        elements.append({"tag": "markdown", "content": "\n".join(details)})
    else:
        model = event.get("model", {})
        details = [
            _card_field("厂商", model.get("provider")),
            _card_field("模型", f"`{model.get('model', '—')}`"),
            _card_field("标准 ID", f"`{model.get('canonical_model', '—')}`"),
            _card_field("服务档位", model.get("service_tier", "standard")),
            _card_field("模态 / 计费", f"{model.get('modality', 'text')} / {model.get('billing_unit', 'token')}"),
            _card_field("地区 / 币种", f"{model.get('region', '—')} / {model.get('currency', '—')}"),
        ]
        if event_name == "model_removed":
            details.append(_card_field("确认次数", f"{event.get('confirmations', 2)} 轮连续健康监控"))
        elements.append({"tag": "markdown", "content": "\n".join(details)})

    source_url = event.get("after", {}).get("source_url") if event_name == "price_changed" else event.get("model", {}).get("source_url")
    if source_url:
        elements.append({"tag": "action", "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": "查看官方来源"}, "type": "default", "multi_url": {"url": source_url}}]})
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": f"数据日期：{event.get('data_date', '—')} · 事件类型：{event_name} · Model Price Hub"}]})
    return {"config": {"wide_screen_mode": True, "enable_forward": True}, "header": {"template": color, "title": {"tag": "plain_text", "content": f"Model Price Hub · {title}"}}, "elements": elements}


def _payload(item: WebhookConfig, event: dict[str, Any]) -> bytes:
    event_payload = {"msg_type": "interactive", "card": _feishu_card(event)} if _is_feishu(item) else event
    return json.dumps(event_payload, ensure_ascii=False, separators=(",", ":")).encode()


def _official_event(event: dict[str, Any]) -> bool:
    """飞书只允许官方模型/官方价格事件；测试事件单独放行。"""
    if event.get("event") == "test":
        return True
    if event.get("event") not in {"price_changed", "model_added", "model_removed"}:
        return False
    payloads = []
    if event.get("model"):
        payloads.append(event["model"])
    payloads.extend(x for x in (event.get("before"), event.get("after")) if x)
    if not payloads:
        return False
    official_channels = {"official", "aliyun-bailian", "volcengine"}
    return all(p.get("channel") in official_channels and p.get("source") in OFFICIAL_SOURCES and p.get("provider") and p.get("model") for p in payloads)


def send_webhook(item: WebhookConfig, event: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(item, event)
    last_error = ""
    for attempt in range(1, 4):
        try:
            with httpx.Client(timeout=10, follow_redirects=False) as client:
                response = client.post(str(item.url), content=payload, headers=_headers(payload, item.secret))
            if 200 <= response.status_code < 300:
                # 飞书即使业务失败也可能返回 HTTP 200，需要继续检查 code。
                if _is_feishu(item):
                    try:
                        result = response.json()
                    except ValueError:
                        result = {}
                    if result.get("code", 0) not in (0, None):
                        last_error = f"飞书错误 code={result.get('code')}: {result.get('msg', '未知错误')}"
                    else:
                        return {"ok": True, "status_code": response.status_code, "attempt": attempt, "remote": result}
                else:
                    return {"ok": True, "status_code": response.status_code, "attempt": attempt}
            else:
                last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # 网络通知失败不能阻断价格入库
            last_error = repr(exc)
        if attempt < 3:
            time.sleep(attempt)
    return {"ok": False, "error": last_error, "attempt": 3}


def _policy_allowed(event: dict[str, Any], policy: Any) -> bool:
    """按后台策略过滤事件类型；notify_third_party 开启时放宽官方来源限制。"""
    kind = event.get("event")
    if kind == "test":
        return True
    if kind == "price_changed" and not policy.notify_price_changes:
        return False
    if kind in {"model_added", "model_removed"} and not policy.notify_model_lifecycle:
        return False
    if policy.notify_third_party:
        return kind in {"price_changed", "model_added", "model_removed"}
    return _official_event(event)


def deliver_events(events: list[dict[str, Any]]) -> dict[str, int]:
    """过滤并投递事件，返回真实投递统计，而不是上游候选数量。"""
    policy = get_policy()
    eligible = [event for event in events if _policy_allowed(event, policy)]
    if not events:
        return {"eligible": 0, "delivered": 0, "succeeded": 0, "failed": 0}
    delivered = succeeded = failed = 0
    items = _read()
    for item in items:
        if not item.enabled:
            continue
        results = []
        for event in eligible:
            if event["event"] not in item.events:
                continue
            result = send_webhook(item, event)
            delivered += 1
            succeeded += int(bool(result.get("ok")))
            failed += int(not result.get("ok"))
            results.append({"event": event["event"], "result": result})
        item.last_delivery = {"at": datetime.now(timezone.utc).isoformat(), "results": results}
    _write(items)
    return {"eligible": len(eligible), "delivered": delivered, "succeeded": succeeded, "failed": failed}


@router.post("/{webhook_id}/test")
def test_webhook(webhook_id: str, admin: dict = Depends(require_admin)):
    items = _read()
    item = next((x for x in items if x.id == webhook_id), None)
    if item is None:
        raise HTTPException(404, "Webhook 不存在")
    event = {"event": "test", "occurred_at": datetime.now(timezone.utc).isoformat(), "source": "model-price-hub", "message": "Webhook 配置测试成功"}
    result = send_webhook(item, event)
    item.last_delivery = {"at": datetime.now(timezone.utc).isoformat(), "results": [{"event": "test", "result": result}]}
    _write(items)
    return result
