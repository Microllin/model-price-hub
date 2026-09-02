"""admin 抓取入口的入库链路测试。

回归点：
  1. 「运行脚本」曾经只解析不写库（_run_one_scraper docstring 写死「不写库」），
     界面显示成功但数据永远进不了 v2.sqlite。
  2. 「全局更新」曾经是循环调用上面那个只读函数，93 家全绿但零入库。
"""
from __future__ import annotations

import asyncio

import pytest

from app import serve_official
from app.models.pricing import Currency, RawPrice, Region


class _FakeScraper:
    source_name = "faux"
    channel = "official"
    provider = "faux"
    source_url = "https://example.com/pricing"
    collection_mode = "html"
    requires_render = False

    def __init__(self, rows):
        self._rows = rows

    async def fetch(self):
        return list(self._rows)


def _raw(model: str, inp: float = 1.0, out: float = 2.0) -> RawPrice:
    return RawPrice(
        provider="faux",
        channel="official",
        model=model,
        region=Region.CN,
        currency=Currency.CNY,
        input_per_1m=inp,
        output_per_1m=out,
        source="faux",
        source_url="https://example.com/pricing",
    )


@pytest.fixture()
def admin_env(tmp_path, monkeypatch):
    """把状态文件与 v2 库都指向临时目录。"""
    monkeypatch.setattr(serve_official, "SCRAPER_STATUS_PATH", tmp_path / "scraper-status.json")

    from app.db import sync_v2
    monkeypatch.setattr(sync_v2, "DB_PATH", tmp_path / "v2.sqlite")

    from app.pipeline import store
    monkeypatch.setattr(store, "load_latest_snapshot", lambda: None)
    monkeypatch.setattr(store, "load_overrides", lambda path=None: [])
    return tmp_path


def test_运行单个脚本会真正写入v2库(admin_env, monkeypatch):
    scraper = _FakeScraper([_raw("faux-v1"), _raw("faux-v2")])
    monkeypatch.setattr(serve_official, "_official_scrapers", lambda: [scraper])

    result = asyncio.run(serve_official._run_one_scraper("faux"))

    assert result["ok"] is True
    assert result["persisted"]["new"] == 2

    import sqlite3
    con = sqlite3.connect(admin_env / "v2.sqlite")
    try:
        models = {r[0] for r in con.execute("select model_id from models")}
    finally:
        con.close()
    assert models == {"faux-v1", "faux-v2"}


def test_状态消息反映入库结果而非仅解析成功(admin_env, monkeypatch):
    scraper = _FakeScraper([_raw("faux-v1")])
    monkeypatch.setattr(serve_official, "_official_scrapers", lambda: [scraper])

    asyncio.run(serve_official._run_one_scraper("faux"))

    st = serve_official._status_store()["faux"]
    assert st["status"] == "ok"
    assert "入库" in st["message"]
    assert st["persisted"]["new"] == 1


def test_入库失败不得报成功(admin_env, monkeypatch):
    scraper = _FakeScraper([_raw("faux-v1")])
    monkeypatch.setattr(serve_official, "_official_scrapers", lambda: [scraper])

    def _boom(*a, **kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(serve_official, "_persist_rows", _boom)

    result = asyncio.run(serve_official._run_one_scraper("faux"))

    assert result["ok"] is False
    assert "database is locked" in result["error"]
    assert serve_official._status_store()["faux"]["status"] == "error"


def test_persist为False时退化为纯解析验证(admin_env, monkeypatch):
    scraper = _FakeScraper([_raw("faux-v1")])
    monkeypatch.setattr(serve_official, "_official_scrapers", lambda: [scraper])

    result = asyncio.run(serve_official._run_one_scraper("faux", persist=False))

    assert result["ok"] is True
    assert result["persisted"] is None
    assert not (admin_env / "v2.sqlite").exists()


def test_全局更新走完整管线而非逐个只读抓取(monkeypatch):
    """run-all 必须委托 pipeline.runner.run_once，它才会写快照 + 两个库。"""
    calls: dict = {}

    async def _fake_run_once(dry_run: bool = False, on_progress=None):
        calls["dry_run"] = dry_run
        on_progress("deepseek", True, 12)
        on_progress("mistral", False, 0)
        return 0

    from app.pipeline import runner
    monkeypatch.setattr(runner, "run_once", _fake_run_once)
    monkeypatch.setattr(serve_official, "_scraper_registry", lambda: [{"name": "deepseek"}, {"name": "mistral"}])
    monkeypatch.setattr(serve_official, "_save_status", lambda name, patch: None)

    asyncio.run(serve_official._run_all_bg())

    state = serve_official._run_all_state
    assert calls["dry_run"] is False       # 不能是 dry-run，否则又不入库
    assert state["running"] is False
    assert state["done"] == 2
    assert state["success"] == 1           # mistral 空结果不算成功
    assert state["error"] is None
    assert state["finished_at"] is not None


def test_全局更新遇到管线互斥会如实报错(monkeypatch):
    async def _locked(dry_run: bool = False, on_progress=None):
        return 2

    from app.pipeline import runner
    monkeypatch.setattr(runner, "run_once", _locked)
    monkeypatch.setattr(serve_official, "_scraper_registry", lambda: [{"name": "deepseek"}])

    asyncio.run(serve_official._run_all_bg())

    assert "运行中" in serve_official._run_all_state["error"]
