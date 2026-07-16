"""价格数据模型:抓取器产出的 RawPrice、规范化的 PriceEntry(Pydantic),及 SQLAlchemy 表。

设计要点:同一逻辑模型在不同渠道/货币下价格不同,故以复合键
(provider, channel, model, region, currency) 唯一标识一条价格记录。
所有单价统一归一到「每 1M tokens」。
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from pydantic import BaseModel, Field, computed_field
from sqlalchemy import JSON, DateTime, Float, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Region(str, enum.Enum):
    INTL = "intl"
    CN = "cn"


class Currency(str, enum.Enum):
    USD = "USD"
    CNY = "CNY"


class Provenance(str, enum.Enum):
    SCRAPED = "scraped"  # 本轮抓取得到
    MANUAL = "manual"    # 来自 overrides.yaml 人工层
    STALE = "stale"      # 抓取值被校验冻结,保留的是上一轮旧值


# ---------------------------------------------------------------------------
# 抓取器产出的原始条目(尚未落库)
# ---------------------------------------------------------------------------
class RawPrice(BaseModel):
    """单个抓取器解析出的一条价格。单价单位:每 1M tokens。"""

    provider: str
    channel: str = "official"
    model: str
    region: Region
    currency: Currency
    input_per_1m: float | None = None
    output_per_1m: float | None = None
    cached_input_per_1m: float | None = None
    cache_write_per_1m: float | None = None
    context_window: int | None = None
    max_output: int | None = None
    source_url: str = ""
    source: str = ""  # 数据源标识(scraper 名),由 runner 注入,用于多源交叉验证

    def key(self) -> tuple[str, str, str, str, str]:
        return (
            self.provider,
            self.channel,
            self.model,
            self.region.value,
            self.currency.value,
        )


# ---------------------------------------------------------------------------
# 规范化后的对外 / 落库条目
# ---------------------------------------------------------------------------
class PriceEntry(BaseModel):
    provider: str
    channel: str
    model: str
    region: Region
    currency: Currency
    input_per_1m: float | None = None
    output_per_1m: float | None = None
    cached_input_per_1m: float | None = None
    cache_write_per_1m: float | None = None
    context_window: int | None = None
    max_output: int | None = None
    source_url: str = ""
    source: str = ""                 # 数据源标识(scraper 名)
    canonical_model: str = ""        # 归一化模型 id,用于跨源匹配
    official: bool = False           # 是否官方直营渠道定价
    provenance: Provenance = Provenance.SCRAPED
    scraped_at: datetime = Field(default_factory=utcnow)

    @property
    def stale(self) -> bool:
        return self.provenance == Provenance.STALE

    @computed_field  # type: ignore[prop-decorator]
    @property
    def via_vision(self) -> bool:
        """该价格是否由截图视觉识别得到(source 前缀 vision-)。序列化到 API/快照。"""
        return self.source.startswith("vision-")

    @classmethod
    def from_raw(cls, raw: RawPrice, provenance: Provenance = Provenance.SCRAPED) -> "PriceEntry":
        from app.models.canonical import canonicalize, is_official

        return cls(
            **raw.model_dump(),
            canonical_model=canonicalize(raw.model),
            official=is_official(raw.channel),
            provenance=provenance,
        )


class Snapshot(BaseModel):
    """一次抓取运行的完整产物,写入 data/snapshots/<date>.json。"""

    data_date: str  # YYYY-MM-DD
    generated_at: datetime = Field(default_factory=utcnow)
    entries: list[PriceEntry] = []


# ---------------------------------------------------------------------------
# SQLAlchemy
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


class PriceEntryRow(Base):
    __tablename__ = "price_entries"
    __table_args__ = (
        UniqueConstraint(
            "provider", "channel", "model", "region", "currency", "source",
            name="uq_price_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(128), index=True)
    canonical_model: Mapped[str] = mapped_column(String(128), index=True, default="")
    region: Mapped[str] = mapped_column(String(8), index=True)
    currency: Mapped[str] = mapped_column(String(8), index=True)
    official: Mapped[bool] = mapped_column(default=False, index=True)

    input_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    cached_input_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    cache_write_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_window: Mapped[int | None] = mapped_column(nullable=True)
    max_output: Mapped[int | None] = mapped_column(nullable=True)

    source_url: Mapped[str] = mapped_column(String(512), default="")
    source: Mapped[str] = mapped_column(String(64), default="", index=True)
    provenance: Mapped[str] = mapped_column(String(16), default="scraped")
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def to_entry(self) -> PriceEntry:
        return PriceEntry(
            provider=self.provider,
            channel=self.channel,
            model=self.model,
            canonical_model=self.canonical_model,
            region=Region(self.region),
            currency=Currency(self.currency),
            official=self.official,
            input_per_1m=self.input_per_1m,
            output_per_1m=self.output_per_1m,
            cached_input_per_1m=self.cached_input_per_1m,
            cache_write_per_1m=self.cache_write_per_1m,
            context_window=self.context_window,
            max_output=self.max_output,
            source_url=self.source_url,
            source=self.source,
            provenance=Provenance(self.provenance),
            scraped_at=self.scraped_at,
        )

    @classmethod
    def from_entry(cls, e: PriceEntry) -> "PriceEntryRow":
        return cls(
            provider=e.provider,
            channel=e.channel,
            model=e.model,
            canonical_model=e.canonical_model,
            region=e.region.value,
            currency=e.currency.value,
            official=e.official,
            input_per_1m=e.input_per_1m,
            output_per_1m=e.output_per_1m,
            cached_input_per_1m=e.cached_input_per_1m,
            cache_write_per_1m=e.cache_write_per_1m,
            context_window=e.context_window,
            max_output=e.max_output,
            source_url=e.source_url,
            source=e.source,
            provenance=e.provenance.value,
            scraped_at=e.scraped_at,
        )
