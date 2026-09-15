"""科大讯飞星火 / 零一万物 Yi 官网抓取器解析测试（离线 fixture）。"""
from __future__ import annotations

from app.models.pricing import Currency, Region
from app.scrapers.iflytek import IflytekScraper
from app.scrapers.yi import YiScraper
from tests.conftest import read_fixture


# ===================================================================
#  科大讯飞 / 星火
# ===================================================================

def _iflytek_parse():
    return IflytekScraper().parse(read_fixture("iflytek_body.txt"))


def test_iflytek_spark_x2():
    by = {r.model: r for r in _iflytek_parse()}
    r = by["spark-x2"]
    assert r.input_per_1m == 2.0 and r.output_per_1m == 2.0
    assert r.provider == "iflytek" and r.channel == "official"
    assert r.currency == Currency.CNY and r.region == Region.CN


def test_iflytek_spark_ultra():
    by = {r.model: r for r in _iflytek_parse()}
    assert by["spark-ultra"].input_per_1m == 0.8


def test_iflytek_spark_pro():
    by = {r.model: r for r in _iflytek_parse()}
    assert by["spark-pro"].input_per_1m == 5.0


def test_iflytek_spark_lite_free():
    by = {r.model: r for r in _iflytek_parse()}
    assert by["spark-lite"].input_per_1m == 0.0


def test_iflytek_total_count():
    rows = _iflytek_parse()
    assert len(rows) >= 4


# ===================================================================
#  零一万物 / Yi
# ===================================================================

def _yi_parse():
    return YiScraper().parse(read_fixture("yi_body.txt"))


def test_yi_lightning():
    by = {r.model: r for r in _yi_parse()}
    r = by["yi-lightning"]
    assert r.input_per_1m == 0.99 and r.output_per_1m == 0.99
    assert r.context_window == 16000
    assert r.provider == "01ai" and r.channel == "official"
    assert r.currency == Currency.CNY and r.region == Region.CN


def test_yi_vision():
    by = {r.model: r for r in _yi_parse()}
    r = by["yi-vision-v2"]
    assert r.input_per_1m == 6.0 and r.context_window == 16000


def test_yi_total_count():
    rows = _yi_parse()
    assert len(rows) >= 2
