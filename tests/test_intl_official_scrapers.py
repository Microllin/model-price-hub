"""OpenAI / Anthropic / Google 官方抓取器解析测试(离线 fixture)。

三家页面均为 SSR,表格直接在 HTML 中;fixture 为真实页面的等结构精简版。
"""
from __future__ import annotations

from app.models.pricing import Currency, Region
from app.scrapers.anthropic import AnthropicScraper
from app.scrapers.google import GoogleScraper
from app.scrapers.openai import OpenAIScraper
from tests.conftest import read_fixture


def _by(rows, model, **cond):
    out = []
    for r in rows:
        if r.model != model:
            continue
        if all(getattr(r, k) == v for k, v in cond.items()):
            out.append(r)
    return out


# ---- Anthropic ----

def test_anthropic_main_table_with_cache_write_tiers():
    rows = AnthropicScraper().parse(read_fixture("anthropic_pricing.html"))
    base = _by(rows, "claude-opus-5", service_tier="standard", cache_state=None)
    assert len(base) == 1
    b = base[0]
    assert b.currency == Currency.USD and b.region == Region.INTL
    assert b.input_per_1m == 5.0 and b.output_per_1m == 25.0
    assert b.cached_input_per_1m == 0.5  # cache hits

    w5 = _by(rows, "claude-opus-5", cache_state="write_5m")
    w1h = _by(rows, "claude-opus-5", cache_state="write_1h")
    assert w5[0].cache_write_per_1m == 6.25   # 1.25x
    assert w1h[0].cache_write_per_1m == 10.0  # 2x


def test_anthropic_model_id_normalized_for_litellm_crosscheck():
    rows = AnthropicScraper().parse(read_fixture("anthropic_pricing.html"))
    models = {r.model for r in rows}
    assert "claude-mythos-5" in models  # "(limited availability)" 已清理
    assert "claude-opus-5" in models    # 点号版本 → 连字符,对齐 litellm id


def test_anthropic_fast_and_batch_tiers():
    rows = AnthropicScraper().parse(read_fixture("anthropic_pricing.html"))
    fast = _by(rows, "claude-opus-5", service_tier="fast")
    assert fast[0].input_per_1m == 10.0 and fast[0].output_per_1m == 50.0
    # 斜杠并列模型拆开
    assert _by(rows, "claude-opus-4-8", service_tier="fast")
    batch = _by(rows, "claude-sonnet-5", service_tier="batch")
    assert batch[0].input_per_1m == 1.0 and batch[0].output_per_1m == 5.0
    # 工具按次计费表不进入 per-token 结果
    assert not any(r.model.startswith("web") for r in rows)


# ---- OpenAI ----

def test_openai_four_service_tiers():
    rows = OpenAIScraper().parse(read_fixture("openai_pricing.html"))
    tiers = {r.service_tier for r in rows if r.model == "gpt-5.6-sol"}
    assert {"standard", "batch", "fast"} <= tiers
    std = _by(rows, "gpt-5.6-sol", service_tier="standard", context_range=None)[0]
    assert std.input_per_1m == 5.0 and std.output_per_1m == 30.0
    assert std.cached_input_per_1m == 0.5 and std.cache_write_per_1m == 6.25
    batch = _by(rows, "gpt-5.6-sol", service_tier="batch", context_range=None)[0]
    assert batch.input_per_1m == 2.5  # 5 折


def test_openai_long_context_and_model_note_cleanup():
    rows = OpenAIScraper().parse(read_fixture("openai_pricing.html"))
    long = _by(rows, "gpt-5.6-sol", service_tier="standard", context_range="long")
    assert long[0].input_per_1m == 10.0 and long[0].output_per_1m == 45.0
    # "(<272K context length)" 注释清理;"-" 缓存写 → None
    g55 = _by(rows, "gpt-5.5", service_tier="standard")
    assert g55 and g55[0].cache_write_per_1m is None


def test_openai_realtime_modality_rows():
    rows = OpenAIScraper().parse(read_fixture("openai_pricing.html"))
    audio = _by(rows, "gpt-realtime-2.1", modality="audio")
    text = _by(rows, "gpt-realtime-2.1", modality="text")
    assert audio[0].input_per_1m == 32.0 and audio[0].output_per_1m == 64.0
    assert text[0].input_per_1m == 4.0  # 组首行模型名沿用到后续空首格行
    # 转写表(非标准结构)不解析
    assert not _by(rows, "gpt-4o-transcribe")


# ---- Google ----

def test_google_promo_price_with_effective_to():
    rows = GoogleScraper().parse(read_fixture("google_pricing.html"))
    std = _by(rows, "gemini-3.7-flash", service_tier="standard")
    assert len(std) == 1
    assert std[0].input_per_1m == 0.75 and std[0].output_per_1m == 3.75
    assert std[0].cached_input_per_1m == 0.075
    assert std[0].effective_to is not None and std[0].effective_to.month == 12  # 促销截止
    batch = _by(rows, "gemini-3.7-flash", service_tier="batch")
    assert batch[0].input_per_1m == 0.375


def test_google_context_tiers_and_storage_fee_excluded():
    rows = GoogleScraper().parse(read_fixture("google_pricing.html"))
    pro = {r.context_range: r for r in _by(rows, "gemini-2.5-pro", service_tier="standard")}
    assert pro["0-200k"].input_per_1m == 1.25 and pro["0-200k"].output_per_1m == 10.0
    assert pro[">200k"].input_per_1m == 2.5 and pro[">200k"].output_per_1m == 15.0
    assert pro["0-200k"].cached_input_per_1m == 0.125
    # 存储费 $4.50/1M·小时 不得混入任何字段
    assert all(r.cached_input_per_1m != 4.5 for r in rows)


def test_google_skips_non_gemini_and_non_token():
    rows = GoogleScraper().parse(read_fixture("google_pricing.html"))
    assert not _by(rows, "imagen-4")  # 图像生成按张计费,跳过
    assert all(r.model.startswith(("gemini", "gemma")) for r in rows)
