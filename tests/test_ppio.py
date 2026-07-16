"""PPIO 派欧云抓取器解析测试(离线渲染后文本 fixture)。"""
from __future__ import annotations

from app.models.pricing import Currency, Region
from app.scrapers.ppio import PPIOScraper
from tests.conftest import read_fixture


def _index(rows):
    return {r.model: r for r in rows}


def test_parses_cny_and_maps_vendor():
    rows = PPIOScraper().parse(read_fixture("ppio_body.txt"))
    idx = _index(rows)

    glm = idx["GLM-4.7"]
    assert glm.provider == "zhipu"
    assert glm.currency == Currency.CNY and glm.region == Region.CN
    assert glm.channel == "ppio"
    assert glm.input_per_1m == 4.0 and glm.output_per_1m == 16.0

    assert idx["Deepseek V3.2"].provider == "deepseek"
    assert idx["Kimi K2 Thinking"].provider == "moonshot"
    assert idx["MiniMax-M2"].provider == "minimax"
    assert idx["MiniMax-M2"].input_per_1m == 2.1 and idx["MiniMax-M2"].output_per_1m == 8.4
    assert idx["Qwen3.6-35B-A3B"].provider == "aliyun"


def test_labels_not_parsed_as_models():
    rows = PPIOScraper().parse(read_fixture("ppio_body.txt"))
    names = {r.model.lower() for r in rows}
    for label in ("hot", "new", "cache read", "上下文", "输入"):
        assert label not in names
