"""API 端到端测试:写一份快照 → 起 TestClient 查询。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.models.pricing import Currency, PriceEntry, Region


def _mk(provider, model, region, currency, inp, outp, channel="official"):
    return PriceEntry(
        provider=provider, channel=channel, model=model,
        region=Region(region), currency=Currency(currency),
        input_per_1m=inp, output_per_1m=outp,
    )


def _seed(tmp_path):
    from app.pipeline.store import write_snapshot
    from app.db.session import upsert_entries
    entries = [
        _mk("deepseek", "deepseek-v4-pro", "cn", "CNY", 3.0, 6.0),
        _mk("deepseek", "deepseek-v4-pro", "intl", "USD", 0.435, 0.87),
        _mk("openai", "gpt-4o", "intl", "USD", 2.5, 10.0),
        _mk("anthropic", "claude-sonnet-4", "intl", "USD", 3.0, 15.0, channel="bedrock"),
    ]
    write_snapshot(entries)
    upsert_entries(entries)


def _client():
    from app.main import app
    return TestClient(app)


def test_health(isolated_data):
    _seed(isolated_data)
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_filter_by_provider_returns_both_currencies(isolated_data):
    _seed(isolated_data)
    r = _client().get("/v1/prices", params={"provider": "deepseek"})
    assert r.status_code == 200
    currencies = {e["currency"] for e in r.json()["entries"]}
    assert currencies == {"CNY", "USD"}


def test_filter_by_currency_cny(isolated_data):
    _seed(isolated_data)
    r = _client().get("/v1/prices", params={"currency": "CNY"})
    entries = r.json()["entries"]
    assert entries and all(e["currency"] == "CNY" for e in entries)


def test_filter_by_channel_bedrock(isolated_data):
    _seed(isolated_data)
    r = _client().get("/v1/prices", params={"channel": "bedrock"})
    entries = r.json()["entries"]
    assert entries and all(e["channel"] == "bedrock" for e in entries)


def test_convert_usd_to_cny(isolated_data):
    _seed(isolated_data)
    r = _client().get("/v1/prices", params={"provider": "openai", "convert": "CNY"})
    e = r.json()["entries"][0]
    assert e["currency"] == "CNY"
    assert e["input_per_1m"] > 2.5  # 已按汇率放大


def test_model_detail_and_404(isolated_data):
    _seed(isolated_data)
    client = _client()
    assert client.get("/v1/prices/deepseek/deepseek-v4-pro").status_code == 200
    assert client.get("/v1/prices/deepseek/does-not-exist").status_code == 404


def test_providers_and_models(isolated_data):
    _seed(isolated_data)
    client = _client()
    provs = {p["provider"] for p in client.get("/v1/providers").json()["providers"]}
    assert {"deepseek", "openai", "anthropic"} <= provs
    models = client.get("/v1/models").json()
    assert models["count"] >= 3
