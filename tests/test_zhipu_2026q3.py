"""智谱新版定价页(2026-09 改版)卡片解析测试。

改版要点:价格从表格改成「模型名 + 若干标签/值」的卡片,原先要点的 7 个 tab
(视觉理解 / Reasoning models / ...)全部消失,换成 3 个 button.tab-pill。
旧解析器只认得旗舰区那几行,条目从 22 掉到 3,触发骤降护栏。

样本 zhipu_body_2026q3.txt 取自改版后的真实页面(逐个点击 3 个 pill 后累积正文)。
旧样本 zhipu_body.txt 保留,由 tests/test_official_scrapers.py 覆盖 _parse_legacy。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.scrapers.zhipu import ZhipuScraper

FIXTURE = Path(__file__).parent / "fixtures" / "zhipu_body_2026q3.txt"


@pytest.fixture(scope="module")
def rows():
    return ZhipuScraper().parse(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def by_model(rows):
    return {r.model: r for r in rows}


def test_改版页面能解析出多个模型(rows):
    """改版前旧解析器只得到 3 条并触发骤降护栏,这里必须明显多于它。"""
    assert len(rows) >= 6


def test_旗舰模型价格正确(by_model):
    r = by_model["GLM-5.3"]
    assert r.input_per_1m == 8.0
    assert r.output_per_1m == 28.0
    assert r.cached_input_per_1m == 2.0
    assert r.context_window == 1_000_000
    assert r.billing_unit == "token"


def test_带单位后缀的价格(by_model):
    """GLM-5.3-Flash 的值写作「0.8元 / M」,含空格。"""
    r = by_model["GLM-5.3-Flash"]
    assert (r.input_per_1m, r.output_per_1m) == (0.8, 2.8)
    assert r.cached_input_per_1m == 0.23


def test_不带单位的价格按token处理(by_model):
    """GLM-5.2 卡片把单位写在列头,值里只剩「8元」。"""
    r = by_model["GLM-5.2"]
    assert (r.input_per_1m, r.output_per_1m) == (8.0, 28.0)
    assert r.billing_unit == "token"


def test_百万Tokens写法(by_model):
    r = by_model["GLM-OCR"]
    assert (r.input_per_1m, r.output_per_1m) == (0.2, 0.2)
    assert r.context_window == 32_000


def test_万字符计费不被当成token(by_model):
    """GLM-TTS 是「2元/万字符」,单位错了会让价格差两个数量级。"""
    r = by_model["GLM-TTS"]
    assert r.input_per_1m == 2.0
    assert r.billing_unit == "10k_chars"


def test_单价型卡片记为输入价(by_model):
    """只有「单价」一个价格字段的卡片(无输入/输出之分)。"""
    r = by_model["GLM-rerank-pro"]
    assert r.input_per_1m == 0.8
    assert r.output_per_1m is None


def test_所有条目都带官方渠道与人民币(rows):
    assert all(r.provider == "zhipu" and r.channel == "official" for r in rows)
    assert all(r.currency.value == "CNY" for r in rows)


def test_不产生重复条目(rows):
    keys = [r.key() for r in rows]
    assert len(keys) == len(set(keys))


def test_常见问题里的模型名不被误当成卡片(by_model):
    """页面底部 FAQ 提到 GLM-4.5、GLM-5V-Turbo 等,但它们没有卡片、没有价格,
    不能凭文中出现就造出条目——那会产出价格为空的脏数据。"""
    for name in ("GLM-4.5", "GLM-5V-Turbo", "GLM-4.6V"):
        assert name not in by_model


def test_旧样本仍走旧逻辑():
    """新卡片解析器对旧版页面必须返回空,好让 parse() 回退到 _parse_legacy;
    否则页面一旦回滚,两套逻辑会互相遮蔽。"""
    legacy = Path(__file__).parent / "fixtures" / "zhipu_body.txt"
    scraper = ZhipuScraper()
    text = legacy.read_text(encoding="utf-8")
    assert scraper._parse_cards(text) == []
    assert len(scraper.parse(text)) > 20
