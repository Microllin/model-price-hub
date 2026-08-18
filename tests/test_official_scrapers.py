"""智谱 / MiniMax 官网抓取器解析测试(离线渲染后文本 fixture)。"""
from __future__ import annotations

from app.models.pricing import Currency, Region
from app.scrapers.minimax import MiniMaxScraper
from app.scrapers.zhipu import ZhipuScraper
from tests.conftest import read_fixture


def test_zhipu_takes_first_tier_official_cny():
    rows = ZhipuScraper().parse(read_fixture("zhipu_body.txt"))
    by_model: dict[str, list] = {}
    for r in rows:
        by_model.setdefault(r.model, []).append(r)
    # 新品旗舰 GLM-5.2 用「新品 1M」格式(无"输入长度"),必须也抓到
    assert "GLM-5.2" in by_model
    assert by_model["GLM-5.2"][0].input_per_1m == 8.0 and by_model["GLM-5.2"][0].output_per_1m == 28.0
    glm51 = {r.context_range: r for r in by_model["GLM-5.1"]}
    assert glm51["0-32k"].provider == "zhipu" and glm51["0-32k"].channel == "official"
    assert glm51["0-32k"].currency == Currency.CNY and glm51["0-32k"].region == Region.CN
    # 首档 [0,32k) 6/24,不取限时折扣 1.3;第二档 [32k+) 8/28 也产出
    assert glm51["0-32k"].input_per_1m == 6.0 and glm51["0-32k"].output_per_1m == 24.0
    assert glm51[">32k"].input_per_1m == 8.0 and glm51[">32k"].output_per_1m == 28.0
    assert by_model["GLM-5"][0].input_per_1m == 4.0 and by_model["GLM-5"][0].output_per_1m == 18.0


def test_minimax_discount_and_skip_video():
    rows = MiniMaxScraper().parse(read_fixture("minimax_body.txt"))
    m3 = {r.context_range: r for r in rows if r.model == "MiniMax-M3"}
    # M3 折扣行取折后价;≤512k 与 >512k 两档都产出
    assert m3["0-512k"].input_per_1m == 2.10 and m3["0-512k"].output_per_1m == 8.40
    assert m3[">512k"].input_per_1m == 4.20 and m3[">512k"].output_per_1m == 16.80
    assert m3["0-512k"].cached_input_per_1m == 0.42
    # M2.7 无阶梯 → context_range 为 None
    m27 = [r for r in rows if r.model == "MiniMax-M2.7"]
    assert len(m27) == 1 and m27[0].context_range is None
    assert m27[0].input_per_1m == 2.1 and m27[0].output_per_1m == 8.4
    assert m27[0].cached_input_per_1m == 0.42
    # 视频模型跳过
    assert "MiniMax-Hailuo-2.3" not in {r.model for r in rows}


def test_official_channel_flag():
    for row in ZhipuScraper().parse(read_fixture("zhipu_body.txt")):
        assert row.channel == "official"
    for row in MiniMaxScraper().parse(read_fixture("minimax_body.txt")):
        assert row.channel == "official"
