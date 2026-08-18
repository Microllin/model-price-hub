"""0.2.1 后台能力测试：登录锁定、策略落地、API Key 强制认证、Webhook 策略过滤。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.models.pricing import Currency, PriceEntry, Provenance, RawPrice, Region


@pytest.fixture()
def admin_client(isolated_data, monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "change-me-123456")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import app
    c = TestClient(app)
    r = c.post("/v1/admin/login", json={"username": "admin", "password": "change-me-123456"})
    assert r.status_code == 200
    return c, tmp_path


def test_login_lockout_after_repeated_failures(isolated_data, monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "change-me-123456")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.api import admin as admin_api
    admin_api._login_failures.clear()
    from app.main import app
    c = TestClient(app)

    for _ in range(5):
        assert c.post("/v1/admin/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    r = c.post("/v1/admin/login", json={"username": "admin", "password": "change-me-123456"})
    assert r.status_code == 429
    admin_api._login_failures.clear()


def test_policy_freeze_ratio_is_consumed_by_validate(monkeypatch, tmp_path):
    """后台把冻结阈值调低后，第三方小幅波动也应被冻结。"""
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "policy.json").write_text(json.dumps({"price_change_freeze_ratio": 0.05}), encoding="utf-8")

    from app.pipeline.validate import validate_and_merge
    prev = [PriceEntry(
        provider="deepseek", channel="openrouter", source="openrouter", model="m1",
        region=Region.CN, currency=Currency.CNY, input_per_1m=1.0, output_per_1m=2.0,
        provenance=Provenance.SCRAPED, official=False,
    )]
    raw = RawPrice(
        provider="deepseek", channel="openrouter", source="openrouter", model="m1",
        region=Region.CN, currency=Currency.CNY, input_per_1m=1.1, output_per_1m=2.0,
    )
    entries, report = validate_and_merge([raw], prev)
    assert len(report.frozen) == 1  # 默认 0.40 不会冻结,策略 0.05 会
    assert entries[0].input_per_1m == 1.0
    assert entries[0].provenance == Provenance.STALE


def test_api_key_required_flow(admin_client):
    c, _ = admin_client
    # 默认关闭：公开可读
    assert c.get("/v1/prices").status_code == 200

    policy = c.get("/v1/admin/policy").json()["policy"]
    policy["api_key_required"] = True
    assert c.put("/v1/admin/policy", json=policy).status_code == 200

    # 开启后：无 Key 401
    assert c.get("/v1/prices").status_code == 401
    assert c.get("/v1/snapshots").status_code == 401

    # 有效 Key 200
    key = c.post("/v1/admin/api-keys", json={"name": "ci", "scopes": ["read"], "rate_limit_per_minute": 2}).json()["api_key"]
    assert c.get("/v1/prices", headers={"X-API-Key": key}).status_code == 200

    # 限流：每分钟 2 次，第 3 次 429
    assert c.get("/v1/providers", headers={"X-API-Key": key}).status_code == 200
    assert c.get("/v1/models", headers={"X-API-Key": key}).status_code == 429

    # 停用后 403
    key_id = c.get("/v1/admin/api-keys").json()["api_keys"][0]["id"]
    c.put(f"/v1/admin/api-keys/{key_id}", json={"name": "ci", "scopes": ["read"], "enabled": False, "rate_limit_per_minute": 100})
    assert c.get("/v1/prices", headers={"X-API-Key": key}).status_code == 403


def test_webhook_policy_filtering(admin_client):
    from app.api.webhooks import deliver_events

    official_event = {
        "event": "price_changed",
        "after": {"provider": "deepseek", "model": "m1", "channel": "official", "source": "deepseek"},
    }
    third_party_event = {
        "event": "price_changed",
        "after": {"provider": "deepseek", "model": "m1", "channel": "openrouter", "source": "openrouter"},
    }

    # 默认策略：官方事件 eligible，第三方被过滤
    stats = deliver_events([official_event, third_party_event])
    assert stats["eligible"] == 1

    # 关闭价格通知：官方价格事件也被过滤
    c, _ = admin_client
    policy = c.get("/v1/admin/policy").json()["policy"]
    policy["notify_price_changes"] = False
    c.put("/v1/admin/policy", json=policy)
    assert deliver_events([official_event])["eligible"] == 0

    # 打开第三方通知并恢复价格通知：第三方事件放行
    policy["notify_price_changes"] = True
    policy["notify_third_party"] = True
    c.put("/v1/admin/policy", json=policy)
    assert deliver_events([third_party_event])["eligible"] == 1
