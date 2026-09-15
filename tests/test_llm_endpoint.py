"""LLM 调用的端点拼接与错误提示测试。

踩过的坑:base_url 填成站点根地址(不带 /v1)时,OpenAI 分支拼出
`<base>/chat/completions`,而不少网关对未知路径会返回 **200 + 前端 HTML**,
于是 raise_for_status 放行、解析 JSON 才炸,报错只有一句 JSONDecodeError,
完全看不出是地址写错了。两类 AI 修复都走这个函数,所以一起失效。
"""
from __future__ import annotations

import json

import pytest

import app.serve_official as so


class _FakeResponse:
    def __init__(self, body: str, ctype: str = "application/json", status: int = 200):
        self._body = body
        self.status_code = status
        self.headers = {"content-type": ctype}
        self.content = body.encode()

    @property
    def text(self) -> str:
        return self._body

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return json.loads(self._body)


class _FakeClient:
    """记录请求落到哪个 URL，并返回预设响应。"""

    calls: list[str] = []
    response = _FakeResponse(json.dumps({"choices": [{"message": {"content": "ok"}}]}))

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        type(self).calls.append(url)
        return type(self).response


@pytest.fixture
def capture(monkeypatch):
    _FakeClient.calls = []
    _FakeClient.response = _FakeResponse(json.dumps({"choices": [{"message": {"content": "ok"}}]}))
    monkeypatch.setattr(so.httpx, "AsyncClient", _FakeClient)
    return _FakeClient


def _key(**over):
    base = {"api_key": "k", "model": "claude-sonnet-4-6", "base_url": "https://example.com"}
    base.update(over)
    return base


# --- 端点拼接 -------------------------------------------------------------

@pytest.mark.asyncio
async def test_openai_协议_base_不带v1时自动补全(capture):
    await so._call_llm(_key(protocol="openai"), "hi")
    assert capture.calls[-1] == "https://example.com/v1/chat/completions"


@pytest.mark.asyncio
async def test_openai_协议_base_已带v1不重复补(capture):
    await so._call_llm(_key(protocol="openai", base_url="https://example.com/v1"), "hi")
    assert capture.calls[-1] == "https://example.com/v1/chat/completions"


@pytest.mark.asyncio
async def test_anthropic_协议_两种写法都对(capture):
    capture.response = _FakeResponse(json.dumps({"content": [{"type": "text", "text": "ok"}]}))
    await so._call_llm(_key(protocol="anthropic"), "hi")
    assert capture.calls[-1] == "https://example.com/v1/messages"
    await so._call_llm(_key(protocol="anthropic", base_url="https://example.com/v1"), "hi")
    assert capture.calls[-1] == "https://example.com/v1/messages"


# --- 非 JSON 响应的报错 ----------------------------------------------------

@pytest.mark.asyncio
async def test_返回HTML时报出人话而不是JSONDecodeError(capture):
    capture.response = _FakeResponse("<!doctype html><html>…", ctype="text/html")
    with pytest.raises(RuntimeError) as exc:
        await so._call_llm(_key(protocol="openai"), "hi")
    msg = str(exc.value)
    assert "不是 JSON" in msg
    assert "base_url" in msg          # 要指明可能的原因
    assert "chat/completions" in msg  # 要带上实际请求的地址便于核对
