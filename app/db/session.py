"""SQLAlchemy 会话与 upsert 逻辑。"""
from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.pricing import Base, PriceEntry, PriceEntryRow

_engine = create_engine(settings.db_url, future=True)
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(_engine)


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
            stmt = select(PriceEntryRow).where(
                PriceEntryRow.provider == e.provider,
                PriceEntryRow.channel == e.channel,
                PriceEntryRow.model == e.model,
                PriceEntryRow.region == e.region.value,
                PriceEntryRow.currency == e.currency.value,
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
                    "source_url", "source", "provenance", "scraped_at",
                ):
                    setattr(row, field, getattr(new_row, field))
            count += 1
    return count


def all_entries() -> list[PriceEntry]:
    init_db()
    with get_session() as session:
        rows = session.scalars(select(PriceEntryRow)).all()
        return [r.to_entry() for r in rows]
