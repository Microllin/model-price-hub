"""canonical 归一化 + 官方判定测试。"""
from __future__ import annotations

from app.models.canonical import canonicalize, is_official


def test_strips_vendor_path():
    assert canonicalize("deepseek-ai/DeepSeek-V4-Pro") == "deepseek-v4-pro"
    assert canonicalize("Pro/moonshotai/Kimi-K2.6") == "kimi-k2.6"
    assert canonicalize("deepseek/deepseek-v4-pro") == "deepseek-v4-pro"


def test_strips_bedrock_prefix_and_suffix():
    assert canonicalize("us.anthropic.claude-sonnet-4-20250514-v1:0") == "claude-sonnet-4"
    assert canonicalize("anthropic.claude-3-5-sonnet-20241022-v2:0") == "claude-3-5-sonnet"


def test_cross_source_alignment():
    # 三个来源的同一模型应归一到同一 id
    a = canonicalize("deepseek-v4-pro")                 # 官网
    b = canonicalize("deepseek/deepseek-v4-pro")        # openrouter
    c = canonicalize("deepseek-ai/DeepSeek-V4-Pro")     # siliconflow
    assert a == b == c == "deepseek-v4-pro"


def test_official_channels():
    assert is_official("official") is True
    assert is_official("aliyun-bailian") is True
    assert is_official("volcengine") is True
    assert is_official("siliconflow") is False
    assert is_official("openrouter") is False
    assert is_official("bedrock") is False
