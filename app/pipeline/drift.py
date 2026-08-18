"""数据质量漂移报告：每次管线运行后产出 data/drift.json。

面向运维的数据质量面，回答四个问题：
- 哪些模型是本轮新增 / 消失的（对比上一快照的 canonical_model 集合）
- 哪些官方条目没有任何其他源能印证（可能归一化失败或全网独家）
- 哪些模型只有第三方旁证、没有官方源（孤儿，官方价格缺失）
- 哪些来源的价格与官方价偏差超过阈值（可能抓错列/单位错误）
- 各维度字段的填充率（维度定义了但没抓到数据，一眼可见）

报告只是信号，不阻断入库；异常由后台「数据质量」页呈现给管理员。
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.models.canonical import canonicalize
from app.models.pricing import PriceEntry

# 第三方参考价相对官方价的允许偏差(聚合平台有加价/补贴,5% 以内视为正常)
DRIFT_THRESHOLD_PCT = 5.0
# 覆盖率统计的维度字段
COVERAGE_FIELDS = (
    "cached_input_per_1m",
    "cache_write_per_1m",
    "context_window",
    "max_output",
    "context_range",
    "time_window",
    "effective_from",
)


def _canon(e: PriceEntry) -> str:
    return e.canonical_model or canonicalize(e.model)


def _is_standard(e: PriceEntry) -> bool:
    """漂移对比只用标准档,避免峰谷/批量等条件价互相误报。"""
    return e.service_tier == "standard" and not e.time_window and not e.context_range


def build_drift_report(entries: list[PriceEntry], previous: list[PriceEntry]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()

    # ---- 新增 / 消失模型 ----
    current_models = {_canon(e) for e in entries}
    previous_models = {_canon(e) for e in previous}
    new_models = sorted(current_models - previous_models)
    removed_models = sorted(previous_models - current_models)

    # ---- 按 canonical 分组 ----
    by_model: dict[str, list[PriceEntry]] = {}
    for e in entries:
        by_model.setdefault(_canon(e), []).append(e)

    # ---- 未匹配官方条目：官方源产出，但全网没有其他源能印证同一模型 ----
    unmatched_official = []
    for model, group in sorted(by_model.items()):
        sources = {e.source for e in group}
        officials = [e for e in group if e.official]
        if officials and len(sources) == 1:
            unmatched_official.append({
                "model": model,
                "provider": officials[0].provider,
                "source": officials[0].source,
                "note": "仅单一来源，无法交叉验证",
            })

    # ---- 孤儿模型：有第三方旁证但没有任何官方条目 ----
    orphan_models = []
    for model, group in sorted(by_model.items()):
        if not any(e.official for e in group):
            orphan_models.append({
                "model": model,
                "providers": sorted({e.provider for e in group}),
                "sources": sorted({e.source for e in group}),
            })

    # ---- 跨源价格漂移：第三方标准档价 vs 官方标准档价 ----
    price_drift = []
    for model, group in sorted(by_model.items()):
        std = [e for e in group if _is_standard(e)]
        official = [e for e in std if e.official and e.input_per_1m]
        third = [e for e in std if not e.official and e.input_per_1m]
        if not official or not third:
            continue
        # 同货币分别对比；第三方取中位数作参考
        by_cur: dict[str, dict[str, list[float]]] = {}
        for e in official:
            by_cur.setdefault(e.currency.value, {}).setdefault("official", []).append(e.input_per_1m)
        for e in third:
            by_cur.setdefault(e.currency.value, {}).setdefault("third", []).append(e.input_per_1m)
        for cur, vals in by_cur.items():
            if not vals.get("official") or not vals.get("third"):
                continue
            off = statistics.median(vals["official"])
            ref = statistics.median(vals["third"])
            if off <= 0:
                continue
            pct = (ref - off) / off * 100
            if abs(pct) > DRIFT_THRESHOLD_PCT:
                price_drift.append({
                    "model": model,
                    "currency": cur,
                    "official_input": round(off, 6),
                    "third_party_input": round(ref, 6),
                    "drift_pct": round(pct, 2),
                    "third_party_sources": sorted({e.source for e in third if e.currency.value == cur}),
                })
    price_drift.sort(key=lambda x: -abs(x["drift_pct"]))

    # ---- 维度覆盖率 ----
    total = len(entries) or 1
    coverage = {
        field: round(sum(1 for e in entries if getattr(e, field, None) is not None) / total * 100, 1)
        for field in COVERAGE_FIELDS
    }

    return {
        "generated_at": now,
        "threshold_pct": DRIFT_THRESHOLD_PCT,
        "counts": {
            "entries": len(entries),
            "models": len(current_models),
            "models_new": len(new_models),
            "models_removed": len(removed_models),
            "unmatched_official": len(unmatched_official),
            "orphan_models": len(orphan_models),
            "price_drift": len(price_drift),
        },
        "dimension_coverage": coverage,
        "new_models": new_models[:100],
        "removed_models": removed_models[:100],
        "unmatched_official": unmatched_official[:100],
        "orphan_models": orphan_models[:100],
        "price_drift": price_drift[:100],
    }


def write_drift_report(report: dict[str, Any]) -> None:
    path = settings.data_dir / "drift.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_drift_report() -> dict[str, Any] | None:
    path = settings.data_dir / "drift.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
