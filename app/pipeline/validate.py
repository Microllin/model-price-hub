"""校验与变更检测 —— 让「全自动抓取」的数据可信。

三道关:
1. 空结果拦截:某抓取器产出 0 条 → 不覆盖,由 runner 保留旧值。
2. 逐条合理性:价格为负/异常大 → 丢弃该条。
3. 超阈值冻结:单价相较上一快照变动超过阈值 → 保留旧值并标记 STALE(needs review),
   防止页面改版导致的错误值污染数据。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings
from app.models.pricing import PriceEntry, Provenance, RawPrice

# 每 1M tokens 单价的合理上界(USD 或 CNY 同量级足够宽松):超过即视为解析错误
_MAX_PRICE_PER_1M = 100_000.0


@dataclass
class ChangeReport:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    frozen: list[str] = field(default_factory=list)  # 超阈值被冻结
    dropped: list[str] = field(default_factory=list)  # 合理性检查丢弃

    def summary(self) -> str:
        return (
            f"新增 {len(self.added)} · 变价 {len(self.changed)} · "
            f"下线 {len(self.removed)} · 冻结 {len(self.frozen)} · 丢弃 {len(self.dropped)}"
        )


def _key(e: PriceEntry | RawPrice) -> tuple:
    # 含 source:让同一模型的不同数据源(视觉/正则/三方/override)共存而非互相覆盖
    return (e.provider, e.channel, e.model, e.region.value, e.currency.value, e.source)


def _label(e: PriceEntry | RawPrice) -> str:
    return f"{e.provider}/{e.channel}/{e.model} [{e.region.value}/{e.currency.value}]"


def _sane(e: RawPrice) -> bool:
    for v in (e.input_per_1m, e.output_per_1m, e.cached_input_per_1m, e.cache_write_per_1m):
        if v is None:
            continue
        if v < 0 or v > _MAX_PRICE_PER_1M:
            return False
    # 至少要有输入或输出价
    return e.input_per_1m is not None or e.output_per_1m is not None


def _changed_too_much(new: PriceEntry, old: PriceEntry, ratio: float) -> bool:
    for f in ("input_per_1m", "output_per_1m"):
        nv, ov = getattr(new, f), getattr(old, f)
        if nv is None or ov is None or ov == 0:
            continue
        if abs(nv - ov) / ov > ratio:
            return True
    return False


def validate_and_merge(
    scraped: list[RawPrice],
    previous: list[PriceEntry],
) -> tuple[list[PriceEntry], ChangeReport]:
    """把本轮抓取值与上一快照对比、校验、合并。

    返回 (最终条目, 变更报告)。被冻结的条目保留旧值并标记 STALE。
    """
    report = ChangeReport()
    prev_by_key = {_key(e): e for e in previous}

    # 合理性过滤
    good: list[RawPrice] = []
    for r in scraped:
        if _sane(r):
            good.append(r)
        else:
            report.dropped.append(_label(r))

    result: dict[tuple, PriceEntry] = {}
    for r in good:
        k = _key(r)
        new_entry = PriceEntry.from_raw(r, provenance=Provenance.SCRAPED)
        old = prev_by_key.get(k)
        if old is None:
            report.added.append(_label(r))
            result[k] = new_entry
        elif _changed_too_much(new_entry, old, settings.price_change_freeze_ratio):
            # 变动过大 → 冻结,保留旧值,标记待人工复核
            frozen = old.model_copy(update={"provenance": Provenance.STALE})
            result[k] = frozen
            report.frozen.append(_label(r))
        else:
            if (old.input_per_1m, old.output_per_1m) != (
                new_entry.input_per_1m, new_entry.output_per_1m
            ):
                report.changed.append(_label(r))
            result[k] = new_entry

    # 本轮抓取里消失、但上轮存在的抓取来源条目 → 记为下线(不自动删,保留旧值标 STALE)
    scraped_keys = {_key(r) for r in good}
    for k, old in prev_by_key.items():
        if k in result:
            continue
        if old.provenance == Provenance.MANUAL:
            continue  # 人工条目由 override 层负责,不在此保留
        if k not in scraped_keys:
            report.removed.append(_label(old))
            result[k] = old.model_copy(update={"provenance": Provenance.STALE})

    return list(result.values()), report


def apply_overrides(
    entries: list[PriceEntry], overrides: list[PriceEntry]
) -> list[PriceEntry]:
    """override 层覆盖/补充:同键以 override 为准(provenance=manual)。"""
    merged = {_key(e): e for e in entries}
    for o in overrides:
        merged[_key(o)] = o
    return list(merged.values())
