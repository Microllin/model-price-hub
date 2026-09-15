"""Perplexity 官方定价抓取器 —— 官方 USD per-token 价。

https://docs.perplexity.ai/guides/pricing 为 SSR 文档页(Mintlify 风格),
表格直接在 HTML 中,plain HTTP 即可。

页面结构:
- 主表:Model | Input ($/M tokens) | Output ($/M tokens) | Search cost
- 搜索按次计费(per request)跳过,只取 per-token 价

降级链:HTML(零 token) → 有头 Playwright
"""
from __future__ import annotations

import re

from app.models.pricing import Currency, RawPrice, Region
from app.scrapers._html import extract_tables_with_context
from app.scrapers.base import BaseScraper, FetchLevel

_NUM = re.compile(r"\$\s*(\d+(?:\.\d+)?)")


def _price(cell: str) -> float | None:
    """'$5.00' / '$1' → float; '-'/空 → None。"""
    cell = cell.strip()
    if not cell or cell == "-":
        return None
    m = _NUM.search(cell.replace(",", ""))
    return float(m.group(1)) if m else None


def _model_id(name: str) -> str:
    s = re.sub(r"\([^)]*\)", "", name).strip().lower()
    return re.sub(r"\s+", "-", s)


class PerplexityScraper(BaseScraper):
    provider = "perplexity"
    channel = "official"
    source_url = "https://docs.perplexity.ai/guides/pricing"
    fetch_chain = [FetchLevel.HTML, FetchLevel.RENDER]

    async def urls(self) -> list[str]:
        return [self.source_url]

    def parse(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        seen: set[str] = set()

        for heading, table in extract_tables_with_context(text):
            # 只解析含 "Token Pricing" 的表
            h = heading.lower()
            if "token" not in h or "pricing" not in h:
                continue

            # 找 Input/Output 列索引
            header = table[0] if table else []
            inp_idx = out_idx = None
            for ci, cell in enumerate(header):
                cl = cell.lower()
                if "input" in cl and ("token" in cl or "1m" in cl):
                    inp_idx = ci
                elif "output" in cl and ("token" in cl or "1m" in cl):
                    out_idx = ci
            if inp_idx is None or out_idx is None:
                continue

            for row in table[1:]:
                if len(row) <= max(inp_idx, out_idx):
                    continue
                raw_name = row[0].strip()
                if not raw_name or raw_name.lower() in ("model", "name"):
                    continue
                model = _model_id(raw_name)
                if model in seen:
                    continue

                inp = _price(row[inp_idx])
                out = _price(row[out_idx])
                if inp is None and out is None:
                    continue

                seen.add(model)
                results.append(RawPrice(
                    provider=self.provider,
                    channel=self.channel,
                    model=model,
                    region=Region.INTL,
                    currency=Currency.USD,
                    input_per_1m=inp,
                    output_per_1m=out,
                    source_url=self.source_url,
                ))

        return results
