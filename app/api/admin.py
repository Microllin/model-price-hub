"""后台管理员认证、策略配置和 API Key 管理。"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.config import settings

router = APIRouter(prefix="/v1/admin", tags=["admin"])
SESSION_COOKIE = "mph_admin_session"
SESSION_TTL = 12 * 3600


def _path(name: str) -> Path:
    return settings.data_dir / name


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 240_000).hex()


def _admin() -> dict[str, Any]:
    value = _read_json(_path("admin.json"), {})
    if value:
        return value
    if not settings.admin_password:
        return {}
    salt = secrets.token_hex(16)
    value = {"username": settings.admin_username, "salt": salt, "password_hash": _hash_password(settings.admin_password, salt), "created_at": datetime.now(timezone.utc).isoformat()}
    _write_json(_path("admin.json"), value)
    return value


def _sessions() -> dict[str, Any]:
    return _read_json(_path("admin-sessions.json"), {})


def _save_sessions(value: dict[str, Any]) -> None:
    _write_json(_path("admin-sessions.json"), value)


def _current_admin(request: Request) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE, "")
    session = _sessions().get(token)
    if not session or session.get("expires_at", 0) < time.time():
        raise HTTPException(401, "请先登录后台")
    return session


def require_admin(request: Request) -> dict[str, Any]:
    return _current_admin(request)


class LoginInput(BaseModel):
    username: str
    password: str


class PasswordInput(BaseModel):
    old_password: str
    new_password: str = Field(min_length=10)


class PolicyInput(BaseModel):
    price_change_freeze_ratio: float = Field(default=0.40, ge=0, le=10)
    official_auto_apply: bool = True
    agent_auto_review: bool = True
    notify_model_lifecycle: bool = True
    notify_price_changes: bool = True
    notify_third_party: bool = False
    discovery_interval_hours: int = Field(default=6, ge=1, le=168)
    catalog_interval_hours: int = Field(default=6, ge=1, le=168)


class ApiKeyInput(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    enabled: bool = True
    rate_limit_per_minute: int = Field(default=120, ge=1, le=100000)


DEFAULT_POLICY = PolicyInput().model_dump()


@router.post("/login")
def login(body: LoginInput, response: Response):
    admin = _admin()
    if not admin:
        raise HTTPException(503, "尚未配置管理员；请设置 MPH_ADMIN_USERNAME 和 MPH_ADMIN_PASSWORD")
    expected = _hash_password(body.password, admin["salt"])
    if not (hmac.compare_digest(body.username, admin["username"]) and hmac.compare_digest(expected, admin["password_hash"])):
        raise HTTPException(401, "用户名或密码错误")
    token = secrets.token_urlsafe(32)
    sessions = _sessions()
    sessions = {k: v for k, v in sessions.items() if v.get("expires_at", 0) > time.time()}
    sessions[token] = {"username": admin["username"], "created_at": time.time(), "expires_at": time.time() + SESSION_TTL}
    _save_sessions(sessions)
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax", secure=settings.admin_cookie_secure)
    return {"ok": True, "username": admin["username"], "expires_in": SESSION_TTL}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE, "")
    sessions = _sessions(); sessions.pop(token, None); _save_sessions(sessions)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
def me(admin: dict = Depends(require_admin)):
    return {"username": admin["username"], "expires_at": admin["expires_at"]}


@router.post("/password")
def change_password(body: PasswordInput, admin: dict = Depends(require_admin)):
    current = _admin()
    if not hmac.compare_digest(_hash_password(body.old_password, current["salt"]), current["password_hash"]):
        raise HTTPException(400, "原密码错误")
    salt = secrets.token_hex(16)
    current.update({"salt": salt, "password_hash": _hash_password(body.new_password, salt), "updated_at": datetime.now(timezone.utc).isoformat()})
    _write_json(_path("admin.json"), current)
    _save_sessions({})
    return {"ok": True, "message": "密码已更新，请重新登录"}


@router.get("/policy")
def get_policy(admin: dict = Depends(require_admin)):
    return {"policy": {**DEFAULT_POLICY, **_read_json(_path("policy.json"), {})}}


@router.put("/policy")
def update_policy(body: PolicyInput, admin: dict = Depends(require_admin)):
    value = body.model_dump()
    _write_json(_path("policy.json"), value)
    return {"policy": value}


def _api_keys() -> list[dict[str, Any]]:
    return _read_json(_path("api-keys.json"), [])


def _public_key(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k != "key_hash"} | {"has_key": True}


@router.get("/api-keys")
def list_api_keys(admin: dict = Depends(require_admin)):
    return {"api_keys": [_public_key(x) for x in _api_keys()]}


@router.post("/api-keys")
def create_api_key(body: ApiKeyInput, admin: dict = Depends(require_admin)):
    key = "mph_" + secrets.token_urlsafe(32)
    item = {"id": secrets.token_urlsafe(9), **body.model_dump(), "key_hash": hashlib.sha256(key.encode()).hexdigest(), "created_at": datetime.now(timezone.utc).isoformat(), "last_used_at": None}
    items = _api_keys(); items.append(item); _write_json(_path("api-keys.json"), items)
    return {**_public_key(item), "api_key": key}


@router.put("/api-keys/{key_id}")
def update_api_key(key_id: str, body: ApiKeyInput, admin: dict = Depends(require_admin)):
    items = _api_keys()
    for i, old in enumerate(items):
        if old["id"] == key_id:
            items[i] = {**old, **body.model_dump(), "updated_at": datetime.now(timezone.utc).isoformat()}
            _write_json(_path("api-keys.json"), items)
            return _public_key(items[i])
    raise HTTPException(404, "API Key 不存在")


@router.delete("/api-keys/{key_id}")
def delete_api_key(key_id: str, admin: dict = Depends(require_admin)):
    items = _api_keys(); remaining = [x for x in items if x["id"] != key_id]
    if len(items) == len(remaining):
        raise HTTPException(404, "API Key 不存在")
    _write_json(_path("api-keys.json"), remaining)
    return {"ok": True}
