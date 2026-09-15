"""前台多源合并的测试。

同一条价格被多个采集器各抓到一次时，前台不该并排显示两行一模一样的价格——
那是我们内部有几个采集器，不是厂商有两个价。

实测依据:视觉识别读得准但读得少(智谱页面 6 个模型只认出 2 个，且每次不同)，
所以主行优先取非视觉源，视觉作为「另一个源也印证了这个数」的佐证。
"""
from __future__ import annotations

from app.serve_official import _merge_by_source


def _row(**over):
    base = {
        "vendor": "zhipu", "model": "GLM-5.3", "currency": "CNY", "billing_unit": "token",
        "service_tier": "standard", "cache_state": None, "context_range": None,
        "deployment_version": None, "time_window": None,
        "input_price": 8.0, "output_price": 28.0,
        "source": "zhipu", "freshness": "fresh", "stale_reason": None,
    }
    base.update(over)
    return base


def test_单来源原样保留():
    out = _merge_by_source([_row()])
    assert len(out) == 1
    assert out[0]["sources"] == ["zhipu"]
    assert out[0]["source_agreement"] == "single"


def test_两个来源同价合并成一行():
    out = _merge_by_source([_row(), _row(source="vision-zhipu")])
    assert len(out) == 1
    assert out[0]["sources"] == ["vision-zhipu", "zhipu"]
    assert out[0]["source_agreement"] == "agree"


def test_主行取非视觉源():
    """视觉每次认出的模型都不一样，拿它当展示值会让价格时有时无。"""
    out = _merge_by_source([_row(source="vision-zhipu"), _row(source="zhipu")])
    assert out[0]["source"] == "zhipu"


def test_来源价格不一致标记为分歧():
    out = _merge_by_source([_row(), _row(source="vision-zhipu", input_price=9.9)])
    assert out[0]["source_agreement"] == "conflict"
    assert out[0]["source"] == "zhipu"
    assert out[0]["input_price"] == 8.0
    assert out[0]["conflicting"][0]["source"] == "vision-zhipu"


def test_极小浮点差不算分歧():
    out = _merge_by_source([_row(input_price=8.0), _row(source="vision-zhipu", input_price=8.00001)])
    assert out[0]["source_agreement"] == "agree"


def test_维度不同的不合并():
    """上下文档位不同是合法的不同价格行，不能合并。"""
    out = _merge_by_source([_row(context_range="[0,32K)"), _row(context_range="[32K+)", input_price=16.0)])
    assert len(out) == 2


def test_已下架与在展分开分组():
    """两者是同一维度在不同时间点的记录，混在一起比会把上一版价格
    误报成「来源之间有分歧」——实测踩到过。"""
    out = _merge_by_source([_row(), _row(input_price=3.0, freshness="delisted")])
    assert len(out) == 2
    assert {o["freshness"] for o in out} == {"fresh", "delisted"}
    assert all(o["source_agreement"] == "single" for o in out)


def test_任一来源新鲜则整行算新鲜():
    out = _merge_by_source([
        _row(freshness="stale", stale_reason="not_refreshed"),
        _row(source="vision-zhipu", freshness="fresh"),
    ])
    assert out[0]["freshness"] == "fresh"
    assert out[0]["stale_reason"] is None


def test_模型名大小写不同也能合并():
    """视觉识别吐小写、网页解析拿到原样大小写，是同一个模型。"""
    out = _merge_by_source([_row(model="GLM-5.3"), _row(model="glm-5.3", source="vision-zhipu")])
    assert len(out) == 1
    assert out[0]["model"] == "GLM-5.3"


def test_空输入不报错():
    assert _merge_by_source([]) == []
