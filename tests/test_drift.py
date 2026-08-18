"""数据质量漂移报告测试。"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.models.pricing import Currency, PriceEntry, Region
from app.pipeline.drift import build_drift_report


def _e(provider, model, canonical, currency, inp, out, channel, source, official, **kw):
    return PriceEntry(
        provider=provider, channel=channel, model=model, canonical_model=canonical,
        region=Region.CN, currency=Currency(currency),
        input_per_1m=inp, output_per_1m=out, official=official, source=source, **kw
    )


def _entries():
    return [
        # 双源印证 + 第三方偏差 2%（正常）
        _e("deepseek", "deepseek-v4-flash", "deepseek-v4-flash", "CNY", 1.0, 2.0, "official", "deepseek", True),
        _e("deepseek", "deepseek-v4-flash", "deepseek-v4-flash", "CNY", 1.02, 2.0, "openrouter", "openrouter", False),
        # 官方独家（未匹配）
        _e("zhipu", "GLM-9", "glm-9", "CNY", 5.0, 20.0, "official", "zhipu", True),
        # 孤儿（只有第三方）
        _e("acme", "acme-pro", "acme-pro", "USD", 1.0, 2.0, "openrouter", "openrouter", False),
        # 第三方偏差 +40%（告警）
        _e("minimax", "MiniMax-M9", "minimax-m9", "CNY", 2.0, 8.0, "official", "minimax", True),
        _e("minimax", "MiniMax-M9", "minimax-m9", "CNY", 2.8, 8.0, "siliconflow", "siliconflow", False),
    ]


def test_drift_report_sections():
    report = build_drift_report(_entries(), [])
    c = report["counts"]
    assert c["entries"] == 6 and c["models"] == 4
    assert c["models_new"] == 4 and c["models_removed"] == 0

    # 未匹配官方：glm-9 单源；deepseek-v4-flash 有双源不在列
    unmatched = {x["model"] for x in report["unmatched_official"]}
    assert "glm-9" in unmatched and "deepseek-v4-flash" not in unmatched

    # 孤儿：acme-pro 无官方源
    assert [x["model"] for x in report["orphan_models"]] == ["acme-pro"]

    # 价格漂移：minimax-m9 +40% 被告警；deepseek 2% 不告警
    drift = {x["model"]: x["drift_pct"] for x in report["price_drift"]}
    assert drift == {"minimax-m9": 40.0}

    # 维度覆盖率：time_window 全空 → 0%
    assert report["dimension_coverage"]["time_window"] == 0.0


def test_drift_new_and_removed_models():
    prev = [_e("deepseek", "deepseek-v3", "deepseek-v3", "CNY", 1.0, 2.0, "official", "deepseek", True)]
    curr = [_e("deepseek", "deepseek-v4", "deepseek-v4", "CNY", 1.0, 2.0, "official", "deepseek", True)]
    report = build_drift_report(curr, prev)
    assert report["new_models"] == ["deepseek-v4"]
    assert report["removed_models"] == ["deepseek-v3"]


def test_drift_ignores_conditional_prices():
    """峰谷/分档等条件价不参与跨源漂移对比，避免误报。"""
    entries = [
        _e("deepseek", "deepseek-v4", "deepseek-v4", "CNY", 1.0, 2.0, "official", "deepseek", True,
           service_tier="scheduled", time_window={"label": "peak"}),
        _e("deepseek", "deepseek-v4", "deepseek-v4", "CNY", 3.0, 6.0, "openrouter", "openrouter", False),
    ]
    report = build_drift_report(entries, [])
    # 官方条目是峰谷条件价，没有标准档官方价 → 不产生漂移告警
    assert report["counts"]["price_drift"] == 0


def test_drift_api(isolated_data, monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "change-me-123456")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import app
    c = TestClient(app)

    assert c.get("/v1/admin/drift").status_code == 401
    c.post("/v1/admin/login", json={"username": "admin", "password": "change-me-123456"})
    assert c.get("/v1/admin/drift").status_code == 404  # 尚无报告

    from app.pipeline.drift import write_drift_report
    write_drift_report(build_drift_report(_entries(), []))
    r = c.get("/v1/admin/drift")
    assert r.status_code == 200
    assert r.json()["counts"]["entries"] == 6
    # 报告落盘位置正确
    assert json.loads((tmp_path / "drift.json").read_text())["counts"]["models"] == 4
