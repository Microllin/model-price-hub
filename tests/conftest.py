"""pytest 公共配置:让测试用独立临时 DB/快照目录,不污染真实 data/。"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def isolated_data(tmp_path, monkeypatch):
    """把 settings 的存储路径指向临时目录,并重载依赖它的模块。"""
    from app import config

    monkeypatch.setattr(config.settings, "db_path", tmp_path / "prices.db")
    monkeypatch.setattr(config.settings, "snapshots_dir", tmp_path / "snapshots")
    monkeypatch.setattr(config.settings, "latest_path", tmp_path / "latest.json")
    monkeypatch.setattr(config.settings, "overrides_path", tmp_path / "overrides.yaml")
    (tmp_path / "snapshots").mkdir()

    # db.session 在导入时已绑定旧 db_url,需重建 engine
    from app.db import session as db_session
    import sqlalchemy
    monkeypatch.setattr(db_session, "_engine", sqlalchemy.create_engine(f"sqlite:///{tmp_path/'prices.db'}", future=True))
    monkeypatch.setattr(
        db_session, "SessionLocal",
        sqlalchemy.orm.sessionmaker(bind=db_session._engine, expire_on_commit=False, future=True),
    )
    return tmp_path


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
