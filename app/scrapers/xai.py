"""xAI (Grok) 官方定价抓取器 —— 官方 USD per-token 价。

https://docs.x.ai/docs/models#models-and-pricing 为 SSR 页面,表格直接在 HTML 中,
plain HTTP 即可。页面结构:

- 主表:Model | Input ($/M tokens) | Output ($/M tokens) + 可选 context length
- 部分模型有 batch pricing / image pricing 等子表

模型 id 归一为 litellm 风格("Grok 3" → "grok-3"),
保证与 LiteLLM 旁证交叉验证可匹配。

降级链:HTML 正则(零 token) → 有头 Playwright → 截图 OCR(兜底)
"""
from __future__ import annotations

import re

from app.models.pricing import Currency, RawPrice, Region
from app.scrapers._html import extract_tables_with_context
from app.scrapers.base import BaseScraper, FetchLevel

_NUM = re.compile(r"\$?([\d.]+)")


def _price(cell: str) -> float | None:
    """'$5.00' / '5.00' → 5.0; '-'/空 → None。"""
    m = _NUM.search(cell.replace(",", ""))
    return float(m.group(1)) if m else None


def _model_id(name: str) -> str:
    """'Grok 3 Mini' → 'grok-3-mini'。"""
    s = re.sub(r"\([^)]*\)", "", name).strip().lower()
    s = re.sub(r"(?<=\d)\.(?=\d)", "-", s)
    return re.sub(r"\s+", "-", s)


class XAIScraper(BaseScraper):
    provider = "xai"
    channel = "official"
    source_url = "https://docs.x.ai/docs/models"
    fetch_chain = [FetchLevel.HTML, FetchLevel.RENDER]

    async def urls(self) -> list[str]:
        return [self.source_url]

    def parse(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        for heading, table in extract_tables_with_context(text):
            h = heading.lower()
            tier = "standard"
            if "batch" in h:
                tier = "batch"

            for row in table:
                if len(row) < 3 or not row[0].strip():
                    continue
                label = row[0].strip().lower()
                if label in ("model", "模型", "name"):
                    continue

                model = _model_id(row[0])
                if not model.startswith("grok"):
                    continue

                inp = _price(row[1])
                out = _price(row[2])
                if inp is None and out is None:
                    continue

                ctx = None
                if len(row) > 3:
                    ctx_match = re.search(r"(\d+(?:,\d+)*)\s*[kK]", row[3].replace(",", ""))
                    if ctx_match:
                        ctx = int(ctx_match.group(1).replace(",", "")) * 1000

                results.append(RawPrice(
                    provider=self.provider,
                    channel=self.channel,
                    model=model,
                    region=Region.INTL,
                    currency=Currency.USD,
                    input_per_1m=inp,
                    output_per_1m=out,
                    context_window=ctx,
                    service_tier=tier,
                    source_url=self.source_url,
                ))
        return results
