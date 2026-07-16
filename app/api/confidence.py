"""官方价格聚合 + 多源交叉验证置信度。

对每个 (canonical_model, region, currency):
- 官方价 = 官方渠道(official=True)条目的中位数(input/output)。
- 置信度 = 有多少个「独立数据源」印证了这个官方价(数值落在容差内),以及官方源之间是否冲突。
    高:≥3 个源印证一致
    中:2 个源印证一致
    低:仅 1 个源
    待核(conflict):官方源之间数值分歧超过容差 → 归为「中」并置 conflict=True
非官方渠道价格(openrouter/siliconflow/bedrock 等)不单独算置信度,但会作为
「旁证源」参与官方价的交叉验证(数值接近即计入印证源)。
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median

from app.models.pricing import PriceEntry

TOLERANCE = 0.15  # 印证一致的相对误差阈值


def is_vision(source: str) -> bool:
    """视觉主源标记(source 前缀 vision-)。"""
    return source.startswith("vision-")


def _rel_close(a: float | None, ref: float | None, tol: float = TOLERANCE) -> bool:
    if a is None or ref is None or ref == 0:
        return False
    return abs(a - ref) / ref <= tol


def _grade(source_count: int, conflict: bool) -> str:
    if conflict:
        return "medium"
    if source_count >= 3:
        return "high"
    if source_count == 2:
        return "medium"
    return "low"


def official_prices(entries: list[PriceEntry]) -> list[dict]:
    """聚合以模型为主体的官方价 + 置信度。

    显示价【优先取视觉主源】(vision-*);无视觉时回退正则官方价的中位数(优雅降级)。
    置信度 = 有多少个独立数据源(视觉/正则/三方旁证)印证了显示价。
    视觉与正则官方价分歧超容差 → conflict(归为中,待核)。
    """
    by_key: dict[tuple, list[PriceEntry]] = defaultdict(list)
    for e in entries:
        if not e.canonical_model:
            continue
        by_key[(e.canonical_model, e.region.value, e.currency.value)].append(e)

    result: list[dict] = []
    for (canon, region, currency), group in by_key.items():
        offs = [e for e in group if e.official]
        if not offs:
            continue

        vision_offs = [e for e in offs if is_vision(e.source)]
        regex_offs = [e for e in offs if not is_vision(e.source)]
        primary = vision_offs or offs  # 视觉优先,否则全部官方

        p_inputs = [e.input_per_1m for e in primary if e.input_per_1m is not None]
        if not p_inputs:
            continue
        official_input = median(p_inputs)
        p_outputs = [e.output_per_1m for e in primary if e.output_per_1m is not None]
        official_output = median(p_outputs) if p_outputs else None

        # 冲突:有视觉也有正则,但正则值偏离视觉显示价超容差
        conflict = bool(vision_offs) and any(
            not _rel_close(e.input_per_1m, official_input) for e in regex_offs
            if e.input_per_1m is not None
        )

        # 印证源:全部条目(含非官方旁证)中 input 落在容差内的去重 source
        corroborating = {
            e.source for e in group if _rel_close(e.input_per_1m, official_input)
        }
        confidence = _grade(len(corroborating), conflict)

        rep = primary[0]
        result.append(
            {
                "canonical_model": canon,
                "provider": rep.provider,
                "model": rep.model,
                "region": region,
                "currency": currency,
                "input_per_1m": official_input,
                "output_per_1m": official_output,
                "context_window": rep.context_window,
                "confidence": confidence,
                "conflict": conflict,
                "primary_source": rep.source,          # 显示价来自哪个源(vision-* 表示视觉主源)
                "via_vision": is_vision(rep.source),    # 显示价是否由截图视觉识别得到
                "source_count": len(corroborating),
                "sources": sorted(corroborating),
                "official_sources": sorted({e.source for e in offs}),
            }
        )

    result.sort(key=lambda d: (d["provider"], d["canonical_model"], d["currency"]))
    return result
