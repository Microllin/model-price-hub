"""火山引擎（字节跳动/豆包）官方抓取器解析测试（离线 fixture）。"""
from __future__ import annotations

from app.models.pricing import Currency, Region
from app.scrapers.volcengine import VolcEngineScraper
from tests.conftest import read_fixture


def test_volcengine_standard_and_batch_tiers():
    rows = VolcEngineScraper().parse(read_fixture("volcengine_pricing.html"))
    tiers = {r.service_tier for r in rows}
    assert "standard" in tiers and "batch" in tiers

    # doubao-seed-evolving 常规档
    se = [r for r in rows if "evolving" in r.model and r.service_tier == "standard"]
    assert se
    assert se[0].provider == "bytedance" and se[0].channel == "official"
    assert se[0].currency == Currency.CNY and se[0].region == Region.CN
    assert se[0].input_per_1m == 6.0 and se[0].output_per_1m == 30.0
    assert se[0].cached_input_per_1m == 1.2


def test_volcengine_context_range_tiers():
    rows = VolcEngineScraper().parse(read_fixture("volcengine_pricing.html"))
    # doubao-seed-2.0-pro 常规档有多个上下文分档
    pro = [r for r in rows if "2.0-pro" in r.model and r.service_tier == "standard"]
    ranges = {r.context_range for r in pro}
    assert len(ranges) >= 2  # 至少 [0,32] 和 (32,128]


def test_volcengine_skips_non_token_tables():
    rows = VolcEngineScraper().parse(read_fixture("volcengine_pricing.html"))
    models = {r.model for r in rows}
    # 视频(seedance)、图片(seedream)、embedding 不应出现
    assert not any("seedance" in m for m in models)
    assert not any("seedream" in m for m in models)
    assert not any("embedding" in m for m in models)
