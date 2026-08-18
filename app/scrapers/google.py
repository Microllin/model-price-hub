"""Google Gemini 官方定价抓取器 —— 官方 USD per-token 价。

https://ai.google.dev/gemini-api/docs/pricing 为 SSR 页面,表格直接在 HTML 中。
页面结构:每个模型一个 h2 标题,其下 h3 为服务档位(Standard/Batch/Flex/Priority),
档位下一张「Free Tier / Paid Tier」表。只解析 Paid Tier 列:

- 分档单元格:"$1.25, prompts <= 200k tokens $2.50, prompts > 200k tokens"
  → 拆成 context_range=0-200k / >200k 两条
- 促销单元格:"$0.75 through December 31, 2026. $1.50"
  → 取当前生效价,effective_to=促销截止日(到期后页面更新,抓取自然跟进)
- 存储费("$4.50 / 1,000,000 tokens per hour")与 grounding 按次计费 → 跳过

模型 id 归一为 litellm 风格("Gemini 2.5 Pro" → "gemini-2.5-pro",保留点号),
保证与 LiteLLM 旁证交叉验证可匹配。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models.pricing import Currency, RawPrice, Region
from app.scrapers._html import extract_tables_with_context
from app.scrapers.base import BaseScraper

# "$1.25, prompts <= 200k tokens" 分档
_TIER = re.compile(r"\$(\d+(?:\.\d+)?)\s*,?\s*prompts\s*(<=?|>=?)\s*(\d+)k", re.I)
# "$0.75 through December 31, 2026." 促销
_PROMO = re.compile(r"\$(\d+(?:\.\d+)?)\s+through\s+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})")
_NUM = re.compile(r"\$(\d+(?:\.\d+)?)")
_STORAGE = re.compile(r"\$[\d.]+\s*/\s*1,000,000 tokens per hour[^$]*", re.I)
_MODEL_PREFIX = re.compile(r"^(gemini|gemma)\b", re.I)

_ROW_MAP = (("input price", "input_per_1m"), ("output price", "output_per_1m"), ("context caching price", "cached_input_per_1m"))


def _model_id(name: str) -> str | None:
    """'Gemini 2.5 Pro' → 'gemini-2.5-pro';非 Gemini/Gemma 系返回 None。"""
    s = re.sub(r"\([^)]*\)", "", name).strip().lower()
    if not _MODEL_PREFIX.match(s):
        return None
    return re.sub(r"\s+", "-", s)


def _paid_cell(cell: str) -> list[tuple[str | None, float, datetime | None]]:
    """解析 Paid Tier 单元格 → [(context_range, price, effective_to)]。"""
    low = cell.lower()
    if not cell.strip() or "free of charge" in low or "not available" in low:
        return []
    cell = _STORAGE.sub("", cell)  # 剔除存储费片段(缓存单元格会混入)
    tiers = _TIER.findall(cell)
    if tiers:
        out = []
        for price, op, bound in tiers:
            cr = f"0-{bound}k" if op.startswith("<") else f">{bound}k"
            out.append((cr, float(price), None))
        return out
    m = _PROMO.search(cell)
    if m:
        try:
            dt = datetime.strptime(f"{m.group(2)} {m.group(3)} {m.group(4)}", "%B %d %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            dt = None
        return [(None, float(m.group(1)), dt)]
    m = _NUM.search(cell)
    return [(None, float(m.group(1)), None)] if m else []


class GoogleScraper(BaseScraper):
    provider = "google"
    channel = "official"
    source_url = "https://ai.google.dev/gemini-api/docs/pricing"

    async def urls(self) -> list[str]:
        return [self.source_url]

    def parse(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        for heading, table in extract_tables_with_context(text):
            parts = [p.strip() for p in heading.split(">")]
            model = _model_id(parts[-2]) if len(parts) >= 2 else _model_id(parts[-1])
            if not model:
                continue
            tier = parts[-1].lower() if len(parts) >= 2 and parts[-1].lower() in ("standard", "batch", "flex", "priority") else "standard"
            # 每个 context_range 聚合 input/output/cache 三行成一条记录
            slots: dict[tuple[str | None, datetime | None], dict] = {}
            for row in table:
                if len(row) < 2:
                    continue
                label = row[0].strip().lower()
                field = next((f for prefix, f in _ROW_MAP if label.startswith(prefix)), None)
                if not field:
                    continue
                for cr, price, eff_to in _paid_cell(row[-1]):
                    slot = slots.setdefault((cr, eff_to), {})
                    slot[field] = price
                    slot["effective_to"] = eff_to
            for (cr, eff_to), vals in slots.items():
                if vals.get("input_per_1m") is None and vals.get("output_per_1m") is None:
                    continue
                results.append(RawPrice(
                    provider=self.provider, channel=self.channel, model=model,
                    region=Region.INTL, currency=Currency.USD,
                    input_per_1m=vals.get("input_per_1m"),
                    output_per_1m=vals.get("output_per_1m"),
                    cached_input_per_1m=vals.get("cached_input_per_1m"),
                    service_tier=tier, context_range=cr,
                    effective_to=vals.get("effective_to"),
                    source_url=self.source_url,
                ))
        return results
