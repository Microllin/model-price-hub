"""app/db/sync_v2.py 的增量写库行为测试。

覆盖回归点：前端 serve_official 读 data/v2.sqlite，但历史上只有手工 import_v2 会写它，
admin「运行脚本 / 全局更新」全程不入库，导致新发布的模型永远进不了界面。
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db.sync_v2 import sync_v2_entries
from app.models.pricing import Currency, PriceEntry, Region


def _entry(model: str, source: str = "anthropic", *, provider: str = "anthropic",
           inp: float | None = 10.0, out: float | None = 50.0,
           service_tier: str = "standard", cache_state: str | None = None) -> PriceEntry:
    return PriceEntry(
        provider=provider,
        channel="official",
        model=model,
        canonical_model=model,
        region=Region.INTL,
        currency=Currency.USD,
        input_per_1m=inp,
        output_per_1m=out,
        service_tier=service_tier,
        cache_state=cache_state,
        source=source,
        source_url="https://example.com/pricing",
        official=True,
    )


def _plans(db_path) -> list[tuple]:
    con = sqlite3.connect(db_path)
    try:
        return list(con.execute(
            "select m.model_id, pp.service_tier, pp.cache_state, pp.input_price,"
            "       pp.output_price, pp.is_current, pp.effective_to"
            "  from price_plans pp join models m on m.id = pp.model_id"
            " order by m.model_id, pp.service_tier, pp.cache_state"
        ))
    finally:
        con.close()


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "v2.sqlite"


def test_首次写入建表并落盘(db):
    stats = sync_v2_entries([_entry("claude-fable-5")], db_path=db)

    assert stats["new"] == 1
    assert stats["providers"] == 1 and stats["models"] == 1
    rows = _plans(db)
    assert len(rows) == 1
    assert rows[0][0] == "claude-fable-5"
    assert rows[0][5] == 1  # is_current


def test_重复同步幂等不产生新行(db):
    entries = [_entry("claude-fable-5"), _entry("claude-fable-5", service_tier="batch", inp=5.0, out=25.0)]
    sync_v2_entries(entries, db_path=db)
    stats = sync_v2_entries(entries, db_path=db)

    assert stats["new"] == 0
    assert stats["changed"] == 0
    assert stats["unchanged"] == 2
    assert len(_plans(db)) == 2


def test_新模型发布后增量补进已有库(db):
    """核心回归：fable 5.1 这类新发布模型必须能追加进已存在的 v2.sqlite。"""
    sync_v2_entries([_entry("claude-fable-5")], db_path=db)

    stats = sync_v2_entries(
        [_entry("claude-fable-5"), _entry("claude-fable-5-1")], db_path=db
    )

    assert stats["new"] == 1
    assert stats["models"] == 1
    assert {r[0] for r in _plans(db)} == {"claude-fable-5", "claude-fable-5-1"}


def test_价格变动走更新而非插入(db):
    sync_v2_entries([_entry("claude-fable-5", inp=10.0)], db_path=db)

    stats = sync_v2_entries([_entry("claude-fable-5", inp=8.0)], db_path=db)

    assert stats["changed"] == 1 and stats["new"] == 0
    rows = _plans(db)
    assert len(rows) == 1
    assert float(rows[0][3]) == 8.0


def test_本源消失的方案被软过期(db):
    sync_v2_entries(
        [_entry("claude-fable-5"), _entry("claude-opus-4")], db_path=db
    )

    stats = sync_v2_entries([_entry("claude-fable-5")], db_path=db)

    assert stats["stale"] == 1
    by_model = {r[0]: r for r in _plans(db)}
    assert by_model["claude-fable-5"][5] == 1
    assert by_model["claude-opus-4"][5] == 0        # is_current 置否
    assert by_model["claude-opus-4"][6] is not None  # effective_to 已填


def test_单源同步不误伤其他厂商(db):
    """admin 里单独跑一个厂商的脚本，不能把别家价格判成下线。"""
    sync_v2_entries(
        [_entry("claude-fable-5"), _entry("deepseek-chat", source="deepseek", provider="deepseek")],
        db_path=db,
    )

    sync_v2_entries([_entry("claude-fable-5")], scope_sources={"anthropic"}, db_path=db)

    by_model = {r[0]: r for r in _plans(db)}
    assert by_model["deepseek-chat"][5] == 1  # 别家仍然有效


def test_软过期后重新出现会复活(db):
    sync_v2_entries([_entry("claude-fable-5"), _entry("claude-opus-4")], db_path=db)
    sync_v2_entries([_entry("claude-fable-5")], db_path=db)

    sync_v2_entries([_entry("claude-fable-5"), _entry("claude-opus-4")], db_path=db)

    by_model = {r[0]: r for r in _plans(db)}
    assert by_model["claude-opus-4"][5] == 1
    assert by_model["claude-opus-4"][6] is None


def test_运行记录写入结束时间(db):
    sync_v2_entries([_entry("claude-fable-5")], db_path=db)

    con = sqlite3.connect(db)
    try:
        runs = list(con.execute(
            "select total_scraped, new_count, finished_at from scrape_runs"
        ))
    finally:
        con.close()
    assert len(runs) == 1
    assert runs[0][0] == 1 and runs[0][1] == 1
    assert runs[0][2] is not None  # 旧 import_v2 从不写 finished_at


def test_模型元信息只补齐不被空值覆盖(db):
    rich = _entry("claude-fable-5")
    rich.context_window = 200_000
    sync_v2_entries([rich], db_path=db)

    sync_v2_entries([_entry("claude-fable-5")], db_path=db)  # 不带 context_window

    con = sqlite3.connect(db)
    try:
        (ctx,) = con.execute("select context_window from models").fetchone()
    finally:
        con.close()
    assert ctx == 200_000
