"""价格新鲜度判定测试。

对应场景见 app/serve_official.py 的 classify_freshness():
  A 源成功、本行被刷新           → fresh
  B 源成功、本行没抓到(软过期)   → delisted
  C 源整体失败                   → stale/source_failed
  D 源部分失败                   → 刷新的 fresh、没刷新的 stale/not_refreshed
  E v1 侧被冻结标 STALE 的条目    → stale/not_refreshed
  F override 人工覆盖(无状态记录)→ 不得判为故障
  G 源 unavailable(配置性跳过)  → 不得判为故障
  H 厂商真的下架                 → delisted，与爬虫故障区分
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.serve_official import FRESHNESS_TOLERANCE, classify_freshness

RUN = datetime(2026, 9, 15, 8, 30, tzinfo=timezone.utc)
OLD = RUN - timedelta(days=7)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _call(scraped_at, *, is_current=True, source="zhipu", status=None, latest=RUN):
    return classify_freshness(
        scraped_at=scraped_at,
        is_current=is_current,
        source=source,
        status_store={source: status} if status else {},
        source_latest={source: latest},
    )


def _ok_status(run=RUN, success=RUN):
    return {"status": "ok", "last_run": _iso(run), "last_success": _iso(success)}


# --- A: 源成功、本行被刷新 -------------------------------------------------

def test_A_刷新过的行是新鲜的():
    assert _call(RUN, status=_ok_status()) == ("fresh", None)


def test_A_同轮内秒级时间差不得判为陈旧():
    """同一轮抓取里每行 scraped_at 天然差几秒。若无容差，
    同源里除最新一条外全会被误标——实测 550 行会误标 533 行。"""
    for delta in (timedelta(seconds=1), timedelta(seconds=45), timedelta(minutes=30)):
        assert _call(RUN - delta, status=_ok_status()) == ("fresh", None)


def test_A_容差边界():
    assert _call(RUN - FRESHNESS_TOLERANCE, status=_ok_status()) == ("fresh", None)
    beyond = RUN - FRESHNESS_TOLERANCE - timedelta(minutes=1)
    assert _call(beyond, status=_ok_status()) == ("stale", "not_refreshed")


# --- B / H: 软过期与厂商下架 ------------------------------------------------

def test_B_软过期的行标为已下架():
    assert _call(OLD, is_current=False, status=_ok_status()) == ("delisted", None)


def test_H_下架优先于抓取故障():
    """厂商下架和爬虫坏了是两码事，下架必须优先，不能混为一谈。"""
    failed = {"status": "error", "last_run": _iso(RUN), "last_success": _iso(OLD)}
    assert _call(OLD, is_current=False, status=failed) == ("delisted", None)


# --- C: 源整体失败 ----------------------------------------------------------

def test_C_源最近一次跑失败则本行沿用旧值():
    failed = {"status": "error", "last_run": _iso(RUN), "last_success": _iso(OLD)}
    assert _call(OLD, status=failed, latest=OLD) == ("stale", "source_failed")


def test_C_源从未成功过也算失败():
    never = {"status": "warning", "last_run": _iso(RUN), "last_success": None}
    assert _call(OLD, status=never, latest=OLD) == ("stale", "source_failed")


# --- D: 源部分失败 ----------------------------------------------------------

def test_D_部分失败时精准区分():
    """同一个源里，刷新了的不该被误伤，没刷新的要标出来。
    这正是实测中智谱的情况:80 行里只有 3 行刷新。"""
    status = _ok_status()
    assert _call(RUN, status=status) == ("fresh", None)
    assert _call(OLD, status=status) == ("stale", "not_refreshed")


# --- E: v1 冻结条目 ---------------------------------------------------------

def test_E_被冻结的条目保留旧时间戳故判为陈旧():
    """validate 冻结价格时保留旧值旧时间，sync_v2 用条目自己的
    scraped_at 写库，因此这类行的时间戳停在历史值。"""
    assert _call(OLD, status=_ok_status()) == ("stale", "not_refreshed")


# --- F: override 人工覆盖 ---------------------------------------------------

def test_F_无状态记录的源不得判为故障():
    """override 是人工维护的数据，scraper-status.json 里没有它的 key。"""
    freshness, reason = _call(RUN, source="override", status=None, latest=RUN)
    assert (freshness, reason) == ("fresh", None)


def test_F_无状态记录时仍按时间比较():
    assert _call(OLD, source="override", status=None) == ("stale", "not_refreshed")


# --- G: 配置性跳过 ----------------------------------------------------------

def test_G_unavailable_不算抓取失败():
    """没配视觉凭据导致的跳过是配置选择，不是脚本坏了，
    报成"抓取失败"属于谎报军情。"""
    skipped = {"status": "unavailable", "last_run": _iso(RUN), "last_success": None}
    assert _call(RUN, status=skipped, latest=RUN) == ("fresh", None)


def test_G_running_和_never_不算失败():
    for state in ("running", "never"):
        status = {"status": state, "last_run": _iso(RUN), "last_success": None}
        assert _call(RUN, status=status, latest=RUN) == ("fresh", None)


def test_G_抓到0条的旧错误信息被降级不算失败():
    """与 _normalize_probe_status() 保持一致:这条错误意味着待复核，
    历史入库数据不受影响，不该当成故障。"""
    status = {
        "status": "error",
        "error": "抓取成功但返回 0 条数据,官方页面结构可能已变更",
        "last_run": _iso(RUN),
        "last_success": _iso(OLD),
    }
    assert _call(RUN, status=status, latest=RUN) == ("fresh", None)


# --- 时区与空值 -------------------------------------------------------------

def test_naive_时间戳按UTC处理():
    """price_plans.scraped_at 是 naive UTC，scraper-status.json 带 +00:00，
    两者必须先统一再比较，否则比较结果没有意义。"""
    naive = RUN.replace(tzinfo=None)
    assert _call(naive, status=_ok_status()) == ("fresh", None)


def test_缺少时间戳时不误报():
    assert _call(None, status=_ok_status()) == ("fresh", None)
    assert _call(RUN, status=_ok_status(), latest=None) == ("fresh", None)
