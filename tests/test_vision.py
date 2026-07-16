"""视觉抓取器归一逻辑测试 —— 用模型返回的 JSON 直接测 rows_to_prices,不打网络/API。"""
from __future__ import annotations

from app.models.pricing import Currency, Region
from app.scrapers.kimi import KimiScraper
from app.scrapers.vision_base import VisionScraper


def test_per_1k_unit_converted_to_per_1m():
    # 模型返回单位为每千 tokens → 应 ×1000 归一到每 1M
    js = '{"rows": [{"model": "ERNIE-X", "input_price": 0.004, "output_price": 0.018, "unit": "per_1k"}]}'

    class Dummy(VisionScraper):
        provider = "baidu"; source_url = "http://x"
    rows = Dummy().rows_to_prices(js)
    assert len(rows) == 1
    assert rows[0].input_per_1m == 4.0 and rows[0].output_per_1m == 18.0


def test_per_1m_kept_and_fields_mapped():
    js = ('{"rows": [{"model": "Kimi K2.7 Code", "input_price": 6.5, "output_price": 27, '
          '"cache_read_price": 1.0, "unit": "per_1m", "context_window": 262144}]}')
    rows = KimiScraper().rows_to_prices(js)
    r = rows[0]
    assert r.provider == "moonshot" and r.channel == "official"
    assert r.region == Region.CN and r.currency == Currency.CNY
    assert r.model == "kimi-k2.7-code"          # map_model 归一
    assert r.input_per_1m == 6.5 and r.output_per_1m == 27
    assert r.cached_input_per_1m == 1.0
    assert r.context_window == 262144


def test_bad_json_and_empty_rows_are_safe():
    class Dummy(VisionScraper):
        provider = "x"; source_url = "http://x"
    assert Dummy().rows_to_prices("not json") == []
    assert Dummy().rows_to_prices('{"rows": []}') == []
    # 缺价的行跳过
    assert Dummy().rows_to_prices('{"rows": [{"model": "m", "unit": "per_1m"}]}') == []


def test_fetch_skips_without_api_key(monkeypatch):
    # 无任何凭据时 fetch 返回空,不报错
    import asyncio
    from app import config
    monkeypatch.setattr(config.settings, "use_playwright", True)
    monkeypatch.setattr(config.settings, "anthropic_api_key", None)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert asyncio.run(KimiScraper().fetch()) == []
