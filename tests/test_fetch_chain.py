"""取页方式(降级链)运行时覆盖的测试。

「降级链 html/render」是开发者术语，运营看不懂也没法调。现在它可以在后台改，
存进 data/fetch-chain-overrides.json，由 BaseScraper._effective_chain 在运行时覆盖
类里写死的默认值——页面改版后从「网页源码」升到「浏览器渲染」往往就能修好，
不该为此改代码重建镜像。

建议可能来自人也可能来自 AI，所以落盘前要校验。
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

import app.scrapers.base as base_mod
from app.scrapers.registry import all_scrapers
from app.serve_official import FETCH_LEVELS, FetchChainInput


def _scraper(name: str):
    return next(s for s in all_scrapers() if s.source_name == name)


@pytest.fixture
def overrides(tmp_path, monkeypatch):
    def _write(payload: dict) -> None:
        (tmp_path / "fetch-chain-overrides.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        monkeypatch.setattr(base_mod.settings, "data_dir", tmp_path)
    return _write


# --- 覆盖解析 -------------------------------------------------------------

def test_没有覆盖时用代码默认值(overrides):
    overrides({})
    assert _scraper("kimi")._effective_chain == ["html"]


def test_覆盖值优先(overrides):
    overrides({"mistral": {"fetch_chain": ["render"]}})
    assert _scraper("mistral")._effective_chain == ["render"]


def test_取数方式随覆盖变化(overrides):
    """collection_mode 是由降级链推导的，覆盖后必须跟着变，
    否则后台会显示成和实际行为不符的取数方式。"""
    overrides({"mistral": {"fetch_chain": ["render"]}})
    assert _scraper("mistral").collection_mode == "browser_required"
    overrides({"mistral": {"fetch_chain": ["html"]}})
    assert _scraper("mistral").collection_mode == "http"


def test_别的采集器不串台(overrides):
    overrides({"mistral": {"fetch_chain": ["render"]}})
    assert _scraper("kimi")._effective_chain == ["html"]


def test_空覆盖回落默认(overrides):
    overrides({"kimi": {"fetch_chain": []}})
    assert _scraper("kimi")._effective_chain == ["html"]


def test_配置文件损坏不影响抓取(overrides, tmp_path, monkeypatch):
    (tmp_path / "fetch-chain-overrides.json").write_text("{坏掉的", encoding="utf-8")
    monkeypatch.setattr(base_mod.settings, "data_dir", tmp_path)
    assert _scraper("kimi")._effective_chain == ["html"]


# --- 落盘前校验 -----------------------------------------------------------

def _validate(name: str, chain: list[str]):
    """复刻接口里的校验分支，避免为此起一个完整 app。"""
    from app.serve_official import _official_scrapers

    scraper = next(s for s in _official_scrapers() if s.source_name == name)
    clean = [str(x).strip().lower() for x in chain if str(x).strip()]
    unknown = [c for c in clean if c not in FETCH_LEVELS]
    if unknown:
        raise HTTPException(400, f"未知的取页方式: {'、'.join(unknown)}")
    if not clean:
        raise HTTPException(400, "至少要选一种取页方式")
    if len(set(clean)) != len(clean):
        raise HTTPException(400, "同一种取页方式不能重复")
    is_vision = name.startswith("vision-") or "vision" in type(scraper).__name__.lower()
    if "vision" in clean and not is_vision:
        raise HTTPException(400, "「截图识别」只适用于视觉采集器")
    return clean


def test_四种取页方式都有中文说明():
    assert set(FETCH_LEVELS) == {"api", "html", "render", "vision"}
    for key, meta in FETCH_LEVELS.items():
        assert meta["label"] and meta["desc"], key


def test_接受合法组合():
    assert _validate("mistral", ["html", "render"]) == ["html", "render"]


def test_拒绝未知取页方式():
    with pytest.raises(HTTPException):
        _validate("mistral", ["magic"])


def test_拒绝空选择():
    with pytest.raises(HTTPException):
        _validate("mistral", [])


def test_拒绝重复项():
    with pytest.raises(HTTPException):
        _validate("mistral", ["html", "html"])


def test_普通脚本不许选截图识别():
    """普通脚本的 fetch 根本不处理 vision 级，选上不会生效，
    却会让人以为配好了——必须在入口挡住。"""
    with pytest.raises(HTTPException) as exc:
        _validate("mistral", ["html", "vision"])
    assert "截图识别" in exc.value.detail


def test_视觉采集器可以选截图识别():
    assert "vision" in _validate("vision-zhipu", ["render", "vision"])


def test_输入会被规范化():
    assert _validate("mistral", ["  HTML  ", "Render"]) == ["html", "render"]
