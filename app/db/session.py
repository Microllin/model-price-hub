"""SQLAlchemy 会话与 upsert 逻辑。"""
from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager

from sqlalchemy import create_engine, delete, inspect, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.pricing import Base, PriceEntry, PriceEntryRow

_engine = create_engine(settings.db_url, future=True)
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def init_db() -> None:
    # 早期版本唯一键未包含价格维度；快照现在支持 peak/off-peak，必须重建旧表，
    # 否则两条 DeepSeek 调度价格会在同步时触发旧唯一键冲突。正式快照是权威数据源。
    legacy_table = False
    with _engine.begin() as conn:
        table_sql = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='price_entries'")).scalar()
        if table_sql and 'UNIQUE (provider, channel, model, region, currency, source)' in table_sql:
            # SQLite 重命名表会保留旧索引名；先移除普通索引，避免新表建同名索引冲突。
            for (index_name,) in conn.execute(text("PRAGMA index_list('price_entries')")):
                if not index_name.startswith('sqlite_autoindex'):
                    conn.execute(text(f'DROP INDEX "{index_name}"'))
            conn.execute(text('ALTER TABLE price_entries RENAME TO price_entries_legacy'))
            legacy_table = True
    Base.metadata.create_all(_engine)
    if legacy_table:
        with _engine.begin() as conn:
            cols = "provider,channel,model,canonical_model,region,currency,official,input_per_1m,output_per_1m,cached_input_per_1m,cache_write_per_1m,context_window,max_output,source_url,source,provenance,scraped_at,extra,service_tier,modality,billing_unit,cache_state,context_range,condition_key,time_window,effective_from,effective_to"
            conn.execute(text(f"INSERT INTO price_entries ({cols}) SELECT {cols} FROM price_entries_legacy"))
            conn.execute(text('DROP TABLE price_entries_legacy'))
    # 兼容已有 SQLite：新价格维度以 nullable/default 列增量迁移，旧快照无需重建。
    inspector = inspect(_engine)
    columns = {c["name"] for c in inspector.get_columns("price_entries")}
    additions = {
        "service_tier": "VARCHAR(32) NOT NULL DEFAULT 'standard'",
        "modality": "VARCHAR(32) NOT NULL DEFAULT 'text'",
        "billing_unit": "VARCHAR(32) NOT NULL DEFAULT 'token'",
        "cache_state": "VARCHAR(32)",
        "context_range": "VARCHAR(64)",
        "condition_key": "VARCHAR(128) NOT NULL DEFAULT ''",
        "time_window": "JSON",
        "effective_from": "DATETIME",
        "effective_to": "DATETIME",
    }
    with _engine.begin() as conn:
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(text(f'ALTER TABLE price_entries ADD COLUMN {name} {definition}'))


@contextmanager
def get_session() -> Iterable[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def upsert_entries(entries: Iterable[PriceEntry]) -> int:
    """按复合键 upsert。返回写入(新增+更新)条数。"""
    init_db()
    count = 0
    with get_session() as session:
        for e in entries:
            condition = PriceEntryRow.condition_key == e.condition_key
            if e.service_tier == "standard" and e.modality == "text" and e.billing_unit == "token" and not e.cache_state and not e.context_range and not e.time_window:
                condition = or_(condition, PriceEntryRow.condition_key == "")
            stmt = select(PriceEntryRow).where(
                PriceEntryRow.provider == e.provider,
                PriceEntryRow.channel == e.channel,
                PriceEntryRow.model == e.model,
                PriceEntryRow.region == e.region.value,
                PriceEntryRow.currency == e.currency.value,
                PriceEntryRow.service_tier == e.service_tier,
                PriceEntryRow.modality == e.modality,
                PriceEntryRow.billing_unit == e.billing_unit,
                PriceEntryRow.cache_state == e.cache_state,
                PriceEntryRow.context_range == e.context_range,
                condition,
                PriceEntryRow.source == e.source,
            )
            row = session.scalars(stmt).one_or_none()
            new_row = PriceEntryRow.from_entry(e)
            if row is None:
                session.add(new_row)
            else:
                for field in (
                    "canonical_model", "official",
                    "input_per_1m", "output_per_1m", "cached_input_per_1m",
                    "cache_write_per_1m", "context_window", "max_output",
                    "service_tier", "modality", "billing_unit", "cache_state",
                    "context_range", "condition_key", "time_window", "effective_from", "effective_to",
                    "source_url", "source", "provenance", "scraped_at",
                ):
                    setattr(row, field, getattr(new_row, field))
            count += 1
    return count


def sync_entries(entries: Iterable[PriceEntry]) -> int:
    """让数据库与正式快照完全一致；先清空旧行再写入，避免已下线价格残留。"""
    values = list(entries)
    init_db()
    with get_session() as session:
        session.execute(delete(PriceEntryRow))
    return upsert_entries(values)


def all_entries() -> list[PriceEntry]:
    init_db()
    with get_session() as session:
        rows = session.scalars(select(PriceEntryRow)).all()
        return [r.to_entry() for r in rows]
