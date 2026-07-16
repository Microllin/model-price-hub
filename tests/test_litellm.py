"""LiteLLM JSON 抓取器解析测试(离线 fixture)。"""
from __future__ import annotations

from app.models.pricing import Currency, Region
from app.scrapers.litellm_json import LiteLLMScraper
from tests.conftest import read_fixture


def _index(rows):
    return {(r.provider, r.channel, r.model): r for r in rows}


def test_filters_and_maps():
    rows = LiteLLMScraper().parse(read_fixture("litellm_sample.json"))
    idx = _index(rows)

    # sample_spec(非 dict)、embedding、非前沿 babbage 都应被过滤
    models = {r.model for r in rows}
    assert "text-embedding-3-large" not in models
    assert "some-old-babbage" not in models

    # gpt-4o:openai/official,per-token → per-1M
    gpt = idx[("openai", "official", "gpt-4o")]
    assert gpt.currency == Currency.USD and gpt.region == Region.INTL
    assert gpt.input_per_1m == 2.5
    assert gpt.output_per_1m == 10.0
    assert gpt.cached_input_per_1m == 1.25


def test_bedrock_region_prefix_resolves_vendor():
    rows = LiteLLMScraper().parse(read_fixture("litellm_sample.json"))
    # us.anthropic.claude... → provider=anthropic, channel=bedrock(去掉 us. 前缀)
    hit = [r for r in rows if r.channel == "bedrock"]
    assert hit and hit[0].provider == "anthropic"
    assert hit[0].cache_write_per_1m == 3.75


def test_gemini_mapped_to_google():
    rows = LiteLLMScraper().parse(read_fixture("litellm_sample.json"))
    g = [r for r in rows if "gemini" in r.model][0]
    assert g.provider == "google" and g.channel == "official"
