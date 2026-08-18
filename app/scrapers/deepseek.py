"""DeepSeek 官方定价抓取器。

页面为静态 SSR 的转置表(模型为列):
  https://api-docs.deepseek.com/quick_start/pricing        → USD($)   → intl
  https://api-docs.deepseek.com/zh-cn/quick_start/pricing  → CNY(元) → cn
两页结构一致,仅货币/语言不同。模型名从表头动态解析,不硬编码。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta

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
        "https://api-docs.deepseek.com/quick_start/pricing/": (Region.INTL, Currency.USD),
        "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/": (Region.CN, Currency.CNY),
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

        effective_from, timezone_name, windows = self._schedule_info(text, region)
        scheduled = self._scheduled_prices(table, n)
        results: list[RawPrice] = []
        if scheduled:
            # 当前官方表把 OFF-PEAK/PEAK 放在基础价格行中；每个模型生成两个条件价。
            for label in ("off-peak", "peak"):
                values = scheduled[label]
                for i, model in enumerate(models):
                    results.append(RawPrice(
                        provider=self.provider, channel=self.channel, model=model,
                        region=region, currency=currency,
                        input_per_1m=self._nth(values["input"], i),
                        output_per_1m=self._nth(values["output"], i),
                        cached_input_per_1m=self._nth(values["cache_hit"], i),
                        context_window=ctx, max_output=out, service_tier="scheduled",
                        time_window={"label": label, "timezone": timezone_name, "windows": windows[label], "effective_from": effective_from.isoformat() if effective_from else None},
                        effective_from=effective_from, source_url=self.source_url,
                    ))
        else:
            for i, model in enumerate(models):
                results.append(RawPrice(
                    provider=self.provider, channel=self.channel, model=model,
                    region=region, currency=currency,
                    input_per_1m=self._nth(prices.get("input"), i),
                    output_per_1m=self._nth(prices.get("output"), i),
                    cached_input_per_1m=self._nth(prices.get("cache_hit"), i),
                    context_window=ctx, max_output=out, source_url=self.source_url,
                ))
        return results

    # ---- helpers ----
    @staticmethod
    def _scheduled_prices(table: list[list[str]], n: int) -> dict[str, dict[str, list[float | None]]]:
        result = {"off-peak": {"cache_hit": [None] * n, "input": [None] * n, "output": [None] * n}, "peak": {"cache_hit": [None] * n, "input": [None] * n, "output": [None] * n}}
        current_metric = None
        labels = {"OFF-PEAK": "off-peak", "PEAK": "peak", "空闲时段": "off-peak", "高峰时段": "peak"}
        metric_names = (("cache hit", "cache_hit"), ("缓存命中", "cache_hit"), ("cache miss", "input"), ("缓存未命中", "input"), ("output tokens", "output"), ("百万tokens输出", "output"), ("tokens输出", "output"))
        for row in table:
            joined = " ".join(row).lower()
            for needle, metric in metric_names:
                if needle in joined:
                    current_metric = metric; break
            label = next((labels[cell.strip().upper()] for cell in row[:-n] if cell.strip().upper() in labels), None)
            if label and current_metric:
                vals = row[-n:]
                result[label][current_metric] = [_to_float(x) for x in vals]
        return result if any(any(v is not None for v in metric) for group in result.values() for metric in group.values()) else {}

    @staticmethod
    def _nth(values: list[float | None] | None, i: int) -> float | None:
        if not values or i >= len(values):
            return None
        return values[i]

    @staticmethod
    def _schedule_info(text: str, region: Region) -> tuple[datetime | None, str, dict[str, list[dict[str, str]]]]:
        if region == Region.CN:
            return (datetime(2026, 8, 17, 0, 0, tzinfo=timezone(timedelta(hours=8))), "Asia/Shanghai", {
                "peak": [{"start": "09:00", "end": "12:00"}, {"start": "14:00", "end": "18:00"}],
                "off-peak": [{"start": "00:00", "end": "09:00"}, {"start": "12:00", "end": "14:00"}, {"start": "18:00", "end": "24:00"}],
            })
        effective = DeepSeekScraper._effective_from(text)
        return (effective, "UTC", {
            "peak": [{"start": "01:00", "end": "04:00"}, {"start": "06:00", "end": "10:00"}],
            "off-peak": [{"start": "00:00", "end": "01:00"}, {"start": "04:00", "end": "06:00"}, {"start": "10:00", "end": "24:00"}],
        })

    @staticmethod
    def _effective_from(text: str) -> datetime | None:
        # HTML 标签会把句子拆成文本节点；先还原可读文本再解析公告。
        plain = re.sub(r"<[^>]+>", " ", text)
        plain = re.sub(r"\s+", " ", plain)
        zh = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2}:\d{2})", plain)
        if zh:
            y, m, d, hm = zh.groups()
            return datetime.strptime(f"{y}-{m}-{d} {hm}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone(timedelta(hours=8)))
        match = re.search(r"(?:take effect|effective|生效).*?(\d{1,2}:\d{2})\s*UTC.*?(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*(\d{4})", plain, re.I | re.S)
        if not match:
            return None
        try:
            return datetime.strptime(f"{match.group(2)} {match.group(3)} {match.group(4)} {match.group(1)}", "%B %d %Y %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

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
