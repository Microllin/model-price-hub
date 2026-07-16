"""通义千问官网抓取器解析测试(离线渲染后文本 fixture)。"""
from __future__ import annotations

from app.models.pricing import Currency, Region
from app.scrapers.aliyun import AliyunScraper

# 模拟渲染后的行序列(模型名 / 地区 / 模式 / 阶梯 / 输入元 / 输出元)
_BODY = """qwen3-max
当前能力等同于qwen3-max-2026-01-23
中国内地
非思考和思考模式
0<Token≤32K
2.5元
10元
qwen3-max-2026-01-23
中国内地
0<Token≤32K
2.5元
10元
qwen-plus
中国内地
0<Token≤128K
0.8元
2元
qwen3-vl-plus
中国内地
0<Token≤32K
1.5元
4.5元
"""


def test_takes_first_tier_alias_official_cny():
    rows = {r.model: r for r in AliyunScraper().parse(_BODY)}
    mx = rows["qwen3-max"]
    assert mx.provider == "aliyun" and mx.channel == "official"
    assert mx.currency == Currency.CNY and mx.region == Region.CN
    assert mx.input_per_1m == 2.5 and mx.output_per_1m == 10.0
    assert rows["qwen-plus"].input_per_1m == 0.8


def test_skips_date_snapshots_and_multimodal():
    rows = {r.model for r in AliyunScraper().parse(_BODY)}
    assert "qwen3-max-2026-01-23" not in rows   # 日期快照跳过(留主别名)
    assert "qwen3-vl-plus" not in rows           # 多模态 vl 跳过
