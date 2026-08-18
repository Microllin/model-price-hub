"""后台认证与管理 API 测试。"""
from fastapi.testclient import TestClient


def test_admin_login_policy_and_api_key(isolated_data, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "change-me-123456")
    monkeypatch.setattr(settings, "data_dir", isolated_data)
    from app.main import app
    c = TestClient(app)

    assert c.get("/v1/admin/me").status_code == 401
    r = c.post("/v1/admin/login", json={"username": "admin", "password": "change-me-123456"})
    assert r.status_code == 200

    policy = c.get("/v1/admin/policy").json()["policy"]
    assert policy["official_auto_apply"] is True

    r = c.put("/v1/admin/policy", json={**policy, "notify_third_party": True})
    assert r.status_code == 200
    assert r.json()["policy"]["notify_third_party"] is True

    r = c.post("/v1/admin/api-keys", json={"name": "CI", "scopes": ["read"]})
    assert r.status_code == 200
    assert r.json()["api_key"].startswith("mph_")
    listed = c.get("/v1/admin/api-keys").json()["api_keys"]
    assert len(listed) == 1 and "key_hash" not in listed[0]

    assert c.post("/v1/admin/logout").status_code == 200
    assert c.get("/v1/admin/me").status_code == 401


def test_admin_config_required(isolated_data, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_password", None)
    monkeypatch.setattr(settings, "data_dir", isolated_data)
    from app.main import app
    c = TestClient(app)
    r = c.post("/v1/admin/login", json={"username": "admin", "password": "x"})
    assert r.status_code == 503
