"""百度文心 / 腾讯混元官网抓取器解析测试(离线渲染后文本 fixture)。"""
from __future__ import annotations

from app.models.pricing import Currency, Region
from app.scrapers.baidu import BaiduScraper
from app.scrapers.tencent import TencentScraper

_BAIDU = (
    "ERNIE 5.1\tERNIE-5.1\t推理服务\t输入（输入<=32k）\t0.004\t-\t元/千tokens\n"
    "输出（输入<=32k）\t0.018\t-\n"
    "ERNIE 4.5 Turbo\tERNIE-4.5-Turbo-32K\t推理服务\t输入\t0.0008\t-\t元/千tokens\n"
    "输出\t0.0032\t-\n"
    "ERNIE 4.5 Turbo VL\tERNIE-4.5-Turbo-VL-32K\t推理服务\t输入\t0.001\t-\t元/千tokens\n"
    "输出\t0.004\t-\n"
)

_TENCENT = """Hunyuan-a13b
输入：0.5元
输出：2元
Hunyuan-t1-vision
输入：3元
输出：9元
"""


def test_baidu_unit_conversion_and_skip_vl():
    rows = {r.model: r for r in BaiduScraper().parse(_BAIDU)}
    # 元/千tokens → 元/百万tokens(×1000)
    assert rows["ERNIE-5.1"].input_per_1m == 4.0 and rows["ERNIE-5.1"].output_per_1m == 18.0
    assert rows["ERNIE-4.5-Turbo-32K"].input_per_1m == 0.8
    assert rows["ERNIE-5.1"].currency == Currency.CNY and rows["ERNIE-5.1"].region == Region.CN
    assert rows["ERNIE-5.1"].channel == "official"
    # VL 多模态跳过
    assert "ERNIE-4.5-Turbo-VL-32K" not in rows


def test_tencent_parses_and_skips_vision():
    rows = {r.model: r for r in TencentScraper().parse(_TENCENT)}
    assert rows["Hunyuan-a13b"].input_per_1m == 0.5 and rows["Hunyuan-a13b"].output_per_1m == 2.0
    assert rows["Hunyuan-a13b"].channel == "official"
    assert "Hunyuan-t1-vision" not in rows
