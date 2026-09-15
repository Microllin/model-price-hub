"""视觉采集器运行时配置覆盖的测试。

AI 视觉修复只产出建议,要真正生效得写进 data/vision-overrides.json,
由 VisionScraper.cfg() 在运行时覆盖类里写死的默认值。走配置而不是改代码:
点一下就生效、可随时回退、不必为改几个选择器重建镜像。

建议来自大模型,不能照单全收——校验的重点是把采集目标锁在本厂商域名内。
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

import app.scrapers.vision_base as vision_base
from app.scrapers.vision_official import ZhipuVisionScraper
from app.serve_official import VisionConfigInput, _validate_vision_config


@pytest.fixture
def scraper():
    return ZhipuVisionScraper()


@pytest.fixture
def overrides(tmp_path, monkeypatch):
    """把覆盖文件指向临时目录,避免动到真实 data/。"""
    def _write(payload: dict) -> None:
        path = tmp_path / "vision-overrides.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(vision_base.settings, "data_dir", tmp_path)
    return _write


# --- cfg() 覆盖解析 -------------------------------------------------------

def test_没有覆盖时用代码默认值(scraper, overrides):
    overrides({})
    assert scraper.cfg("tab_selectors") == []
    assert scraper.cfg("max_shots_per_page") == 10


def test_覆盖值优先于默认值(scraper, overrides):
    overrides({"vision-zhipu": {"tab_selectors": ["模型", "搜索"], "max_shots_per_page": 15}})
    assert scraper.cfg("tab_selectors") == ["模型", "搜索"]
    assert scraper.cfg("max_shots_per_page") == 15


def test_只覆盖部分字段其余仍走默认(scraper, overrides):
    overrides({"vision-zhipu": {"max_shots_per_page": 20}})
    assert scraper.cfg("max_shots_per_page") == 20
    assert scraper.cfg("screenshot_urls") == []


def test_别的采集器的配置不会串台(scraper, overrides):
    overrides({"vision-baidu": {"tab_selectors": ["不该出现"]}})
    assert scraper.cfg("tab_selectors") == []


def test_配置文件损坏时回落默认值(scraper, tmp_path, monkeypatch):
    """配置读不出来最多是没生效,绝不能让抓取直接崩掉。"""
    (tmp_path / "vision-overrides.json").write_text("{坏掉的 json", encoding="utf-8")
    monkeypatch.setattr(vision_base.settings, "data_dir", tmp_path)
    assert scraper.cfg("tab_selectors") == []


# --- 落盘前的校验 ---------------------------------------------------------

def test_接受同域名的截图地址(scraper):
    body = VisionConfigInput(screenshot_urls=["https://open.bigmodel.cn/pricing/detail"])
    assert _validate_vision_config(scraper, body)["screenshot_urls"] == [
        "https://open.bigmodel.cn/pricing/detail"
    ]


def test_拒绝跨域名的截图地址(scraper):
    """建议由大模型生成,一次误判就能让采集器去抓任意网址,必须锁死域名。"""
    body = VisionConfigInput(screenshot_urls=["https://evil.example.com/x"])
    with pytest.raises(HTTPException) as exc:
        _validate_vision_config(scraper, body)
    assert exc.value.status_code == 400
    assert "域名" in exc.value.detail


def test_拒绝非_http_协议(scraper):
    body = VisionConfigInput(screenshot_urls=["file:///etc/passwd"])
    with pytest.raises(HTTPException):
        _validate_vision_config(scraper, body)


@pytest.mark.parametrize("shots", [0, 31, 999])
def test_截图张数越界被拒(scraper, shots):
    with pytest.raises(HTTPException):
        _validate_vision_config(scraper, VisionConfigInput(max_shots_per_page=shots))


@pytest.mark.parametrize("shots", [1, 15, 30])
def test_截图张数边界内通过(scraper, shots):
    assert _validate_vision_config(scraper, VisionConfigInput(max_shots_per_page=shots))[
        "max_shots_per_page"
    ] == shots


def test_tab_数量过多被拒(scraper):
    body = VisionConfigInput(tab_selectors=[f"tab{i}" for i in range(21)])
    with pytest.raises(HTTPException):
        _validate_vision_config(scraper, body)


def test_tab_文案过长被拒(scraper):
    with pytest.raises(HTTPException):
        _validate_vision_config(scraper, VisionConfigInput(tab_selectors=["x" * 41]))


def test_空白_tab_被清掉(scraper):
    body = VisionConfigInput(tab_selectors=["模型", "  ", "", "知识库"])
    assert _validate_vision_config(scraper, body)["tab_selectors"] == ["模型", "知识库"]


def test_什么都不给则报错(scraper):
    with pytest.raises(HTTPException):
        _validate_vision_config(scraper, VisionConfigInput())
