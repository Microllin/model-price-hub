"""置信度多源交叉验证测试。"""
from __future__ import annotations

from app.api.confidence import official_prices
from app.models.pricing import Currency, PriceEntry, Region


def _e(provider, model, canon, currency, inp, out, channel, source, official, region="intl"):
    return PriceEntry(
        provider=provider, channel=channel, model=model, canonical_model=canon,
        region=Region(region), currency=Currency(currency), official=official,
        input_per_1m=inp, output_per_1m=out, source=source,
    )


def test_single_official_source_is_low():
    entries = [_e("anthropic", "claude-4-opus", "claude-4-opus", "USD", 15, 75, "official", "litellm", True)]
    rows = official_prices(entries)
    assert len(rows) == 1
    assert rows[0]["confidence"] == "low"
    assert rows[0]["source_count"] == 1


def test_two_agreeing_sources_is_medium():
    entries = [
        _e("deepseek", "deepseek-v4-pro", "deepseek-v4-pro", "USD", 0.435, 0.87, "official", "deepseek", True),
        _e("deepseek", "deepseek-v4-pro", "deepseek-v4-pro", "USD", 0.44, 0.87, "openrouter", "openrouter", False),
    ]
    rows = official_prices(entries)
    assert rows[0]["confidence"] == "medium"
    assert rows[0]["source_count"] == 2
    assert set(rows[0]["sources"]) == {"deepseek", "openrouter"}


def test_three_agreeing_sources_is_high():
    entries = [
        _e("deepseek", "deepseek-v4-flash", "deepseek-v4-flash", "CNY", 1.0, 2.0, "official", "deepseek", True, "cn"),
        _e("deepseek", "DeepSeek-V4-Flash", "deepseek-v4-flash", "CNY", 1.0, 2.0, "siliconflow", "siliconflow", False, "cn"),
        _e("deepseek", "deepseek-v4-flash", "deepseek-v4-flash", "CNY", 1.02, 2.0, "aggr", "override", False, "cn"),
    ]
    rows = official_prices(entries)
    assert rows[0]["confidence"] == "high"
    assert rows[0]["source_count"] == 3


def test_no_official_source_excluded():
    # 只有非官方渠道 → 不进官方视图
    entries = [_e("zhipu", "glm-5.2", "glm-5.2", "CNY", 6, 28, "siliconflow", "siliconflow", False, "cn")]
    assert official_prices(entries) == []


def test_conflicting_vision_vs_regex_flagged():
    # 视觉主源与正则验证器分歧超容差 → conflict
    entries = [
        _e("x", "m", "m", "USD", 10, 20, "official", "vision-x", True),
        _e("x", "m", "m", "USD", 30, 20, "official", "srcB", True),  # 正则,差异 >15%
    ]
    rows = official_prices(entries)
    assert rows[0]["conflict"] is True
    assert rows[0]["confidence"] == "medium"
    assert rows[0]["input_per_1m"] == 10  # 显示价取视觉


def test_vision_is_primary_display():
    # 视觉与正则都在,显示价取视觉;两源一致 → 中置信
    entries = [
        _e("minimax", "MiniMax-M2.7", "minimax-m2.7", "CNY", 2.1, 8.4, "official", "vision-minimax", True, "cn"),
        _e("minimax", "MiniMax-M2.7", "minimax-m2.7", "CNY", 2.1, 8.4, "official", "minimax", True, "cn"),
    ]
    rows = official_prices(entries)
    assert rows[0]["primary_source"] == "vision-minimax"
    assert rows[0]["input_per_1m"] == 2.1
    assert rows[0]["confidence"] == "medium"        # vision + regex 两源印证
    assert set(rows[0]["sources"]) == {"vision-minimax", "minimax"}
    assert rows[0]["conflict"] is False


def test_regex_fallback_without_vision():
    # 无视觉时正则兜底为官方价(优雅降级),单源 → 低
    entries = [
        _e("baidu", "ERNIE-5.1", "ernie-5.1", "CNY", 4, 18, "official", "baidu", True, "cn"),
    ]
    rows = official_prices(entries)
    assert rows[0]["primary_source"] == "baidu"
    assert rows[0]["input_per_1m"] == 4
    assert rows[0]["confidence"] == "low"
