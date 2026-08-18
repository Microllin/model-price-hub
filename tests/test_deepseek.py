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


def test_scheduled_peak_offpeak_prices_are_separate_dimensions():
    # 当前官方页面把峰谷价格并入基础表，按“指标 + 时段 + 模型价格列”转置。
    html = '''<p>The new prices take effect at 16:00 UTC on August 16, 2026.</p><table><tr><td>MODEL</td><td>deepseek-v4-flash</td><td>deepseek-v4-pro</td></tr>
    <tr><td>1M INPUT TOKENS (CACHE HIT)</td><td>OFF-PEAK</td><td>$0.007</td><td>$0.022</td></tr>
    <tr><td>PEAK</td><td>$0.014</td><td>$0.044</td></tr>
    <tr><td>1M INPUT TOKENS (CACHE MISS)</td><td>OFF-PEAK</td><td>$0.22</td><td>$0.66</td></tr>
    <tr><td>PEAK</td><td>$0.44</td><td>$1.32</td></tr>
    <tr><td>1M OUTPUT TOKENS</td><td>OFF-PEAK</td><td>$0.66</td><td>$1.98</td></tr>
    <tr><td>PEAK</td><td>$1.32</td><td>$3.96</td></tr></table>'''
    rows = DeepSeekScraper().parse(html)
    scheduled = [r for r in rows if r.service_tier == "scheduled"]
    assert len(scheduled) == 4
    assert {r.time_window["label"] for r in scheduled} == {"peak", "off-peak"}
    assert {r.input_per_1m for r in scheduled} == {0.22, 0.44, 0.66, 1.32}
    assert {r.cached_input_per_1m for r in scheduled} == {0.007, 0.014, 0.022, 0.044}
    assert all(r.effective_from is not None for r in scheduled)


def test_effective_time_is_parsed_from_notice():
    rows = DeepSeekScraper().parse('''<p>The new prices take effect at 16:00 UTC on August 16, 2026:</p><table><tr><td>MODEL</td><td>deepseek-v4-flash</td></tr><tr><td>1M INPUT TOKENS (CACHE HIT)</td><td>$0.0028</td></tr><tr><td>1M INPUT TOKENS (CACHE MISS)</td><td>$0.14</td></tr><tr><td>1M OUTPUT TOKENS</td><td>$0.28</td></tr></table>''')
    assert DeepSeekScraper._effective_from('The new prices take effect at 16:00 UTC on August 16, 2026:').isoformat() == "2026-08-16T16:00:00+00:00"


def test_china_schedule_windows():
    from app.models.pricing import Region
    effective, tz, windows = DeepSeekScraper._schedule_info("", Region.CN)
    assert tz == "Asia/Shanghai"
    assert effective.isoformat() == "2026-08-17T00:00:00+08:00"
    assert windows["peak"] == [{"start": "09:00", "end": "12:00"}, {"start": "14:00", "end": "18:00"}]
