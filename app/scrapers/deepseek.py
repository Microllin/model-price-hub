"""DeepSeek 官方定价抓取器。

页面为静态 SSR 的转置表(模型为列):
  https://api-docs.deepseek.com/quick_start/pricing        → USD($)   → intl
  https://api-docs.deepseek.com/zh-cn/quick_start/pricing  → CNY(元) → cn
两页结构一致,仅货币/语言不同。模型名从表头动态解析,不硬编码。
"""
from __future__ import annotations

import re

from app.models.pricing import Currency, RawPrice, Region
from app.scrapers._html import extract_tables
from app.scrapers.base import BaseScraper

_NUM = re.compile(r"[-+]?\d*\.?\d+")


def _to_float(cell: str) -> float | None:
    """'0.02元' / '$0.14' / '￥1' → float;无数字返回 None。"""
    m = _NUM.search(cell.replace(",", ""))
    return float(m.group()) if m else None


def _to_tokens(cell: str) -> int | None:
    """'1M' → 1_000_000,'384K' → 384_000。"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*([KkMm])", cell)
    if not m:
        n = _to_float(cell)
        return int(n) if n is not None else None
    val = float(m.group(1))
    mult = 1_000_000 if m.group(2).lower() == "m" else 1_000
    return int(val * mult)


def _row_contains(row: list[str], *needles: str) -> bool:
    joined = " ".join(row).lower()
    return any(n.lower() in joined for n in needles)


class DeepSeekScraper(BaseScraper):
    provider = "deepseek"
    channel = "official"
    source_url = "https://api-docs.deepseek.com/quick_start/pricing"

    _URLS = {
        "https://api-docs.deepseek.com/quick_start/pricing": (Region.INTL, Currency.USD),
        "https://api-docs.deepseek.com/zh-cn/quick_start/pricing": (Region.CN, Currency.CNY),
    }

    async def urls(self) -> list[str]:
        return list(self._URLS)

    # parse 单页时需知道货币/地区;fetch 会分别抓每个 URL,这里用页面内容判定货币。
    def parse(self, text: str) -> list[RawPrice]:
        tables = extract_tables(text)
        # 选中含「MODEL/模型」表头且含定价行的表
        table = self._pick_pricing_table(tables)
        if not table:
            return []

        region, currency = self._detect_currency(text)
        models = self._model_names(table)
        if not models:
            return []

        n = len(models)
        ctx = out = None
        prices: dict[str, list[float | None]] = {}
        for row in table:
            if _row_contains(row, "context length", "上下文长度"):
                ctx = _to_tokens(row[-1])
            elif _row_contains(row, "max output", "输出长度"):
                out = _to_tokens(row[-1])
            elif _row_contains(row, "cache hit", "缓存命中"):
                prices["cache_hit"] = [_to_float(c) for c in row[-n:]]
            elif _row_contains(row, "cache miss", "缓存未命中"):
                prices["input"] = [_to_float(c) for c in row[-n:]]
            elif _row_contains(row, "output tokens", "百万tokens输出", "tokens输出"):
                prices["output"] = [_to_float(c) for c in row[-n:]]

        results: list[RawPrice] = []
        for i, model in enumerate(models):
            results.append(
                RawPrice(
                    provider=self.provider,
                    channel=self.channel,
                    model=model,
                    region=region,
                    currency=currency,
                    input_per_1m=self._nth(prices.get("input"), i),
                    output_per_1m=self._nth(prices.get("output"), i),
                    cached_input_per_1m=self._nth(prices.get("cache_hit"), i),
                    context_window=ctx,
                    max_output=out,
                    source_url=self.source_url,
                )
            )
        return results

    # ---- helpers ----
    @staticmethod
    def _nth(values: list[float | None] | None, i: int) -> float | None:
        if not values or i >= len(values):
            return None
        return values[i]

    @staticmethod
    def _detect_currency(text: str) -> tuple[Region, Currency]:
        # 中文页含「元」且无「$」定价 → CNY;否则 USD
        if "元</td>" in text or "元</" in text or "百万tokens" in text:
            return Region.CN, Currency.CNY
        return Region.INTL, Currency.USD

    @staticmethod
    def _pick_pricing_table(tables: list[list[list[str]]]) -> list[list[str]] | None:
        for t in tables:
            flat = " ".join(" ".join(r) for r in t).lower()
            if ("model" in flat or "模型" in flat) and (
                "output" in flat or "输出" in flat
            ):
                return t
        return None

    @staticmethod
    def _model_names(table: list[list[str]]) -> list[str]:
        for row in table:
            if row and row[0].strip().lower() in ("model", "模型"):
                # 首格为 MODEL/模型,其余为模型名;去掉脚注标记如 "(1)"
                return [re.sub(r"\s*\(\d+\)\s*$", "", c).strip() for c in row[1:] if c.strip()]
        return []
