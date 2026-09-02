"""价格监控中心 v2 数据模型 —— 4 表架构。

表结构：
  providers    → 厂商/渠道元信息
  models       → 模型目录（逻辑模型，与 provider 绑定）
  price_plans  → 定价方案（同模型可有多个方案，按维度拆分）
  scrape_runs  → 抓取运行记录

设计原则：
  - 数据驱动：基于 22 个 scraper 的实际产出字段设计
  - 不做单位转换：billing_unit 标明计费单位，前端按需展示
  - 时序用 is_current + effective_to 软过期，不建历史表
  - region/currency 强绑定（CN=CNY, INTL=USD），但仍分字段存储以备扩展
"""
from __future__ import annotations

import enum
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RegionEnum(str, enum.Enum):
    CN = "cn"
    INTL = "intl"


class CurrencyEnum(str, enum.Enum):
    CNY = "CNY"
    USD = "USD"


class ProviderType(str, enum.Enum):
    VENDOR = "vendor"          # 模型厂商（DeepSeek、智谱…）
    RESELLER = "reseller"      # 转售渠道（SiliconFlow、PPIO…）
    AGGREGATOR = "aggregator"  # 聚合平台（OpenRouter、LiteLLM…）


class BillingUnit(str, enum.Enum):
    TOKEN = "token"            # 元/百万tokens 或 $/M tokens
    REQUEST = "request"        # 元/次 或 $/request
    SECOND = "second"          # 元/秒
    MINUTE = "minute"          # 元/分钟
    PER_IMAGE = "per_image"    # 元/张
    PER_VOICE = "per_voice"    # 元/音色
    CHARS_10K = "10k_chars"    # 元/万字符
    CHATS_10K = "10k_chats"    # 元/万次对话
    GB_HOUR = "gb_hour"        # 元/GB/小时


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# providers — 厂商/渠道
# ---------------------------------------------------------------------------

class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True,
                                       comment="唯一标识，如 deepseek / openrouter")
    display_name: Mapped[str] = mapped_column(String(128),
                                               comment="展示名，如 DeepSeek / OpenRouter")
    type: Mapped[ProviderType] = mapped_column(Enum(ProviderType), default=ProviderType.VENDOR,
                                                comment="vendor / reseller / aggregator")
    homepage_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    pricing_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True,
                                                        comment="官方定价页 URL")
    region: Mapped[str] = mapped_column(String(8), default="both",
                                         comment="cn / intl / both")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # relationships
    models: Mapped[list["Model"]] = relationship(back_populates="provider", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# models — 模型目录
# ---------------------------------------------------------------------------

class Model(Base):
    __tablename__ = "models"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_id", name="uq_provider_model"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), index=True)
    model_id: Mapped[str] = mapped_column(String(128), index=True,
                                           comment="API 调用用的 model name")
    display_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True,
                                                         comment="展示名")
    canonical: Mapped[str] = mapped_column(String(128), index=True, default="",
                                            comment="归一化名，跨渠道比价键")
    modality: Mapped[str] = mapped_column(String(32), default="text",
                                           comment="text/multimodal/image/video/tts/asr/embedding/rerank/search/storage/training")
    context_window: Mapped[Optional[int]] = mapped_column(Integer, nullable=True,
                                                           comment="最大上下文 token 数")
    max_output: Mapped[Optional[int]] = mapped_column(Integer, nullable=True,
                                                       comment="最大输出 token 数")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    release_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    sunset_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True,
                                                         comment="下线日期")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # relationships
    provider: Mapped["Provider"] = relationship(back_populates="models")
    price_plans: Mapped[list["PricePlan"]] = relationship(back_populates="model", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# price_plans — 定价方案
# ---------------------------------------------------------------------------

def _tw_hash(tw: dict | None) -> str:
    """time_window 的确定性哈希，用于唯一约束。"""
    if not tw:
        return ""
    return hashlib.md5(json.dumps(tw, sort_keys=True).encode()).hexdigest()[:16]


class PricePlan(Base):
    __tablename__ = "price_plans"
    __table_args__ = (
        UniqueConstraint(
            "model_id", "channel", "currency", "service_tier",
            "cache_state", "context_range", "deployment_version", "billing_unit", "source", "tw_hash",
            name="uq_price_plan",
        ),
        Index("ix_price_current", "model_id", "is_current"),
        Index("ix_price_channel", "channel"),
        Index("ix_price_scraped", "scraped_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"), index=True)

    # --- 维度字段 ---
    channel: Mapped[str] = mapped_column(String(64), index=True,
                                          comment="official / openrouter / siliconflow ...")
    currency: Mapped[CurrencyEnum] = mapped_column(Enum(CurrencyEnum))
    region: Mapped[RegionEnum] = mapped_column(Enum(RegionEnum))
    billing_unit: Mapped[BillingUnit] = mapped_column(Enum(BillingUnit), default=BillingUnit.TOKEN)
    service_tier: Mapped[str] = mapped_column(String(32), default="standard",
                                               comment="standard/batch/flex/fast/scheduled/lora/full...")
    cache_state: Mapped[Optional[str]] = mapped_column(String(32), nullable=True,
                                                        comment="write_5m / write_1h 等缓存条件")
    context_range: Mapped[Optional[str]] = mapped_column(String(64), nullable=True,
                                                          comment="0-32k / >32k / in:0-32k+out:>0.2k")
    deployment_version: Mapped[Optional[str]] = mapped_column(String(128), nullable=True,
                                                                comment="厂商部署版本，如 8k-int8 / 200k-fp8")
    time_window: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True,
                                                         comment='{"label":"off-peak","windows":[...]}')
    tw_hash: Mapped[str] = mapped_column(String(16), default="",
                                          comment="time_window 确定性哈希，唯一约束用")

    # --- 价格字段 ---
    input_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True,
                                                            comment="输入单价，单位由 billing_unit 决定")
    output_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True,
                                                             comment="输出单价，NULL=统一价/不适用")
    cached_read: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True,
                                                            comment="缓存命中读价")
    cached_write: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True,
                                                             comment="缓存写入价")

    # --- 元信息 ---
    source: Mapped[str] = mapped_column(String(64), default="",
                                         comment="数据来源标识 zhipu/vision-zhipu/litellm")
    source_url: Mapped[str] = mapped_column(String(512), default="")
    effective_from: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    effective_to: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True,
                                                              comment="NULL=当前有效")
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True,
                                              comment="同维度仅一条 TRUE")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # relationships
    model: Mapped["Model"] = relationship(back_populates="price_plans")

    @property
    def is_official(self) -> bool:
        return self.channel == "official"

    @property
    def via_vision(self) -> bool:
        return self.source.startswith("vision-")


# ---------------------------------------------------------------------------
# scrape_runs — 抓取运行记录
# ---------------------------------------------------------------------------

class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_date: Mapped[date] = mapped_column(Date, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    total_scraped: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_count: Mapped[int] = mapped_column(Integer, default=0)
    stale_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    run_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True,
                                                        comment="运行配置快照")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
