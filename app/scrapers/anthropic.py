"""Anthropic 官方定价抓取器 —— 官方 USD per-token 价。

https://docs.anthropic.com/en/docs/about-claude/pricing 为 SSR 页面(Mintlify),
表格直接在 HTML 中,plain HTTP 即可。按表前标题区分三个表:

- 「Model pricing」主表:Model / Base Input / 5m Cache Writes / 1h Cache Writes /
  Cache Hits / Output —— 缓存写双档拆成 cache_state=write_5m/write_1h 两条条件价
- 「Fast mode pricing」:service_tier=fast(2x 标准价)
- 「Batch processing」:service_tier=batch(5 折)

模型显示名归一为 litellm 风格 id("Claude Opus 4.8" → "claude-opus-4-8"),
保证与 LiteLLM 旁证交叉验证可匹配。工具按次计费表(web search 等)不属
per-token 维度,跳过。
"""
from __future__ import annotations

import re

from app.models.pricing import Currency, RawPrice, Region
from app.scrapers._html import extract_tables_with_context
from app.scrapers.base import BaseScraper

_NUM = re.compile(r"[-+]?\d*\.?\d+")


def _price(cell: str) -> float | None:
    """'$10 / MTok' → 10.0;空/'—' → None。"""
    m = _NUM.search(cell.replace(",", ""))
    return float(m.group()) if m else None


def _model_id(name: str) -> str:
    """'Claude Mythos 5 (limited availability)' → 'claude-mythos-5'(对齐 litellm id)。"""
    s = re.sub(r"\([^)]*\)", "", name).strip().lower()
    s = re.sub(r"(?<=\d)\.(?=\d)", "-", s)  # 4.8 → 4-8
    return re.sub(r"\s+", "-", s)


class AnthropicScraper(BaseScraper):
    provider = "anthropic"
    channel = "official"
    source_url = "https://docs.anthropic.com/en/docs/about-claude/pricing"

    async def urls(self) -> list[str]:
        return [self.source_url]

    def parse(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        for heading, table in extract_tables_with_context(text):
            h = heading.lower()
            if "model pricing" in h:
                results.extend(self._main_table(table))
            elif "fast mode" in h:
                results.extend(self._tier_table(table, "fast"))
            elif "batch" in h:
                results.extend(self._tier_table(table, "batch"))
        return results

    def _row(self, model: str, **kw) -> RawPrice:
        return RawPrice(
            provider=self.provider, channel=self.channel, model=model,
            region=Region.INTL, currency=Currency.USD, source_url=self.source_url, **kw
        )

    def _main_table(self, table: list[list[str]]) -> list[RawPrice]:
        out: list[RawPrice] = []
        for row in table[1:]:
            if len(row) < 6 or not row[0].strip():
                continue
            model = _model_id(row[0])
            if not model.startswith("claude"):
                continue
            inp, cw5, cw1h, hit, outp = (_price(c) for c in row[1:6])
            if inp is None and outp is None:
                continue
            out.append(self._row(model, input_per_1m=inp, output_per_1m=outp, cached_input_per_1m=hit))
            if cw5 is not None:
                out.append(self._row(model, cache_write_per_1m=cw5, cache_state="write_5m"))
            if cw1h is not None:
                out.append(self._row(model, cache_write_per_1m=cw1h, cache_state="write_1h"))
        return out

    def _tier_table(self, table: list[list[str]], tier: str) -> list[RawPrice]:
        """Fast/Batch 表:Model / Input / Output;模型格可能斜杠并列多个模型。"""
        out: list[RawPrice] = []
        for row in table[1:]:
            if len(row) < 3:
                continue
            inp, outp = _price(row[-2]), _price(row[-1])
            if inp is None and outp is None:
                continue
            for name in row[0].split("/"):
                model = _model_id(name)
                if model.startswith("claude"):
                    out.append(self._row(model, input_per_1m=inp, output_per_1m=outp, service_tier=tier))
        return out
