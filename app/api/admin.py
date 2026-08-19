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
from app.policy import DEFAULT_POLICY, Policy, get_policy, save_policy

router = APIRouter(prefix="/v1/admin", tags=["admin"])
SESSION_COOKIE = "mph_admin_session"
SESSION_TTL = 12 * 3600

# ---- 登录防爆破：同一账号连续失败锁定 ----
MAX_LOGIN_FAILS = 5
LOGIN_LOCK_SECONDS = 600
_login_failures: dict[str, list[float]] = {}  # username -> [失败时间戳]


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


# 策略模型统一定义在 app.policy，后台保存后由管线/Webhook 实时消费。
PolicyInput = Policy


class ApiKeyInput(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    enabled: bool = True
    rate_limit_per_minute: int = Field(default=120, ge=1, le=100000)


@router.post("/login")
def login(body: LoginInput, response: Response):
    admin = _admin()
    if not admin:
        raise HTTPException(503, "尚未配置管理员；请设置 MPH_ADMIN_USERNAME 和 MPH_ADMIN_PASSWORD")
    now = time.time()
    fails = [t for t in _login_failures.get(body.username, []) if now - t < LOGIN_LOCK_SECONDS]
    if len(fails) >= MAX_LOGIN_FAILS:
        retry = int(LOGIN_LOCK_SECONDS - (now - fails[0]))
        raise HTTPException(429, f"失败次数过多，账号已锁定，请 {max(retry, 1)} 秒后重试")
    expected = _hash_password(body.password, admin["salt"])
    if not (hmac.compare_digest(body.username, admin["username"]) and hmac.compare_digest(expected, admin["password_hash"])):
        fails.append(now)
        _login_failures[body.username] = fails
        raise HTTPException(401, "用户名或密码错误")
    _login_failures.pop(body.username, None)
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
def get_policy_api(admin: dict = Depends(require_admin)):
    return {"policy": get_policy().model_dump()}


@router.get("/drift")
def get_drift(admin: dict = Depends(require_admin)):
    """最近一次管线运行的数据质量漂移报告。"""
    from app.pipeline.drift import read_drift_report
    report = read_drift_report()
    if report is None:
        raise HTTPException(404, "尚无漂移报告；请先运行一次抓取管线")
    return report


@router.get("/agent-config")
def get_agent_config(admin: dict = Depends(require_admin)):
    """视觉/Agent 模型接入配置。"""
    return {"config": _read_json(_path("agent-config.json"), {"base_url": "", "api_key": "", "vision_model": "claude-sonnet-4-6"})}


class AgentConfigInput(BaseModel):
    base_url: str = ""
    api_key: str = ""
    vision_model: str = "claude-sonnet-4-6"


@router.put("/agent-config")
def update_agent_config(body: AgentConfigInput, admin: dict = Depends(require_admin)):
    """保存视觉/Agent 模型接入配置，立即生效。"""
    value = body.model_dump()
    _write_json(_path("agent-config.json"), value)
    return {"config": value}


@router.put("/policy")
def update_policy(body: PolicyInput, admin: dict = Depends(require_admin)):
    return {"policy": save_policy(body).model_dump()}


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


# ---- 只读 API 的 Key 认证（策略 api_key_required 开启后生效）----

_rate_counters: dict[str, list[int]] = {}  # key_id -> [分钟窗口, 计数]


def require_api_key(request: Request) -> dict[str, Any] | None:
    """公开只读端点的可选认证。策略未开启时直接放行。"""
    if not get_policy().api_key_required:
        return None
    key = request.headers.get("X-API-Key", "")
    if not key:
        raise HTTPException(401, "缺少 X-API-Key；请在后台创建 API Key 或关闭 api_key_required 策略")
    digest = hashlib.sha256(key.encode()).hexdigest()
    items = _api_keys()
    item = next((x for x in items if x.get("key_hash") == digest), None)
    if item is None or not item.get("enabled", True):
        raise HTTPException(403, "API Key 无效或已停用")
    if "read" not in item.get("scopes", []):
        raise HTTPException(403, "API Key 缺少 read 权限")
    minute = int(time.time() // 60)
    window, count = _rate_counters.get(item["id"], [minute, 0])
    if window != minute:
        window, count = minute, 0
    count += 1
    _rate_counters[item["id"]] = [window, count]
    if count > item.get("rate_limit_per_minute", 120):
        raise HTTPException(429, "API Key 超出每分钟限流")
    if count == 1:  # 每个限流窗口最多落盘一次，避免高频写 JSON
        item["last_used_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(_path("api-keys.json"), items)
    return item
