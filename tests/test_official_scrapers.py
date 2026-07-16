"""智谱 / MiniMax 官网抓取器解析测试(离线渲染后文本 fixture)。"""
from __future__ import annotations

from app.models.pricing import Currency, Region
from app.scrapers.minimax import MiniMaxScraper
from app.scrapers.zhipu import ZhipuScraper
from tests.conftest import read_fixture


def test_zhipu_takes_first_tier_official_cny():
    rows = {r.model: r for r in ZhipuScraper().parse(read_fixture("zhipu_body.txt"))}
    # 新品旗舰 GLM-5.2 用「新品 1M」格式(无"输入长度"),必须也抓到
    assert "GLM-5.2" in rows
    assert rows["GLM-5.2"].input_per_1m == 8.0 and rows["GLM-5.2"].output_per_1m == 28.0
    glm51 = rows["GLM-5.1"]
    assert glm51.provider == "zhipu" and glm51.channel == "official"
    assert glm51.currency == Currency.CNY and glm51.region == Region.CN
    # 取首档 [0,32),不取折扣价 1.3 或 [32+) 档
    assert glm51.input_per_1m == 6.0 and glm51.output_per_1m == 24.0
    assert rows["GLM-5"].input_per_1m == 4.0 and rows["GLM-5"].output_per_1m == 18.0


def test_minimax_discount_and_skip_video():
    rows = {r.model: r for r in MiniMaxScraper().parse(read_fixture("minimax_body.txt"))}
    # M3 折扣行取折后价(2.10 / 8.40),取首档 ≤512k
    assert rows["MiniMax-M3"].input_per_1m == 2.10
    assert rows["MiniMax-M3"].output_per_1m == 8.40
    # M2.7 单值
    assert rows["MiniMax-M2.7"].input_per_1m == 2.1 and rows["MiniMax-M2.7"].output_per_1m == 8.4
    assert rows["MiniMax-M2.7"].cached_input_per_1m == 0.42
    # 视频模型跳过
    assert "MiniMax-Hailuo-2.3" not in rows


def test_official_channel_flag():
    for row in ZhipuScraper().parse(read_fixture("zhipu_body.txt")):
        assert row.channel == "official"
    for row in MiniMaxScraper().parse(read_fixture("minimax_body.txt")):
        assert row.channel == "official"
