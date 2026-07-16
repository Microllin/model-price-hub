"""DeepSeek 抓取器解析测试(离线 fixture)。"""
from __future__ import annotations

from app.models.pricing import Currency, Region
from app.scrapers.deepseek import DeepSeekScraper
from tests.conftest import read_fixture


def _by_model(rows):
    return {r.model: r for r in rows}


def test_parse_en_usd():
    rows = DeepSeekScraper().parse(read_fixture("deepseek_en.html"))
    assert len(rows) == 2
    m = _by_model(rows)
    flash = m["deepseek-v4-flash"]
    assert flash.currency == Currency.USD
    assert flash.region == Region.INTL
    assert flash.input_per_1m == 0.14          # cache miss = 输入价
    assert flash.output_per_1m == 0.28
    assert flash.cached_input_per_1m == 0.0028  # cache hit
    assert flash.context_window == 1_000_000
    assert flash.max_output == 384_000


def test_parse_zh_cny():
    rows = DeepSeekScraper().parse(read_fixture("deepseek_zh.html"))
    assert len(rows) == 2
    m = _by_model(rows)
    pro = m["deepseek-v4-pro"]
    assert pro.currency == Currency.CNY
    assert pro.region == Region.CN
    assert pro.input_per_1m == 3.0
    assert pro.output_per_1m == 6.0
    assert pro.cached_input_per_1m == 0.025


def test_footnote_stripped_from_model_name():
    rows = DeepSeekScraper().parse(read_fixture("deepseek_en.html"))
    assert all("(" not in r.model for r in rows)
