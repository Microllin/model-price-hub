"""OpenRouter 抓取器解析测试(离线 fixture)。"""
from __future__ import annotations

from app.models.pricing import Currency, Region
from app.scrapers.openrouter import OpenRouterScraper
from tests.conftest import read_fixture


def _index(rows):
    return {(r.provider, r.model): r for r in rows}


def test_maps_cn_vendors_and_prices():
    rows = OpenRouterScraper().parse(read_fixture("openrouter_sample.json"))
    idx = _index(rows)

    # z-ai → zhipu,per-token → per-1M USD
    glm = idx[("zhipu", "glm-5.2")]
    assert glm.currency == Currency.USD and glm.region == Region.INTL
    assert glm.channel == "openrouter"
    assert glm.input_per_1m == 0.6
    assert glm.output_per_1m == 2.2

    # qwen → aliyun
    assert ("aliyun", "qwen3.7-plus") in idx
    # deepseek 缓存价
    assert idx[("deepseek", "deepseek-v4-pro")].cached_input_per_1m == 0.13


def test_skips_covered_providers_and_free_and_unknown():
    rows = OpenRouterScraper().parse(read_fixture("openrouter_sample.json"))
    models = {(r.provider, r.model) for r in rows}
    # openai 已被 LiteLLM 覆盖 → 跳过
    assert not any(p == "openai" for p, _ in models)
    # :free 变体跳过
    assert not any("llama" in m for _, m in models)
    # 未知 vendor 跳过
    assert not any(p == "some-unknown-vendor" for p, _ in models)
