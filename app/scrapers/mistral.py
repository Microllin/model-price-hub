"""Mistral AI 官方定价抓取器 —— 官方 USD per-token 价。

https://mistral.ai/products/la-plateforme#pricing 为 SSR/ISR 页面。
也可通过 API 获取模型列表(https://api.mistral.ai/v1/models,无需 key),
但 API 不含价格,仍需从网页解析。

页面结构:按模型分区,每区一个表:
  Model | Input ($/M tokens) | Output ($/M tokens) | Context

降级链:HTML 正则 → 有头 Playwright
"""
from __future__ import annotations

import re

from app.models.pricing import Currency, RawPrice, Region
from app.scrapers._html import extract_tables_with_context
from app.scrapers.base import BaseScraper, FetchLevel

_NUM = re.compile(r"[\$€]?\s*([\d.]+)")
_CTX = re.compile(r"(\d+(?:[,.]?\d+)*)\s*[kK]")

# Mistral 模型前缀
_VALID_PREFIXES = ("mistral", "codestral", "pixtral", "ministral", "mixtral", "open-mistral", "open-mixtral")


def _price(cell: str) -> float | None:
    m = _NUM.search(cell.replace(",", ""))
    return float(m.group(1)) if m else None


def _model_id(name: str) -> str:
    """'Mistral Large' → 'mistral-large'。"""
    s = re.sub(r"\([^)]*\)", "", name).strip().lower()
    return re.sub(r"\s+", "-", s)


def _context(cell: str) -> int | None:
    m = _CTX.search(cell.replace(",", ""))
    if m:
        return int(float(m.group(1))) * 1000
    return None


class MistralScraper(BaseScraper):
    provider = "mistral"
    channel = "official"
    source_url = "https://mistral.ai/products/la-plateforme"
    fetch_chain = [FetchLevel.HTML, FetchLevel.RENDER]

    async def urls(self) -> list[str]:
        return [self.source_url]

    def parse(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        seen: set[str] = set()

        for heading, table in extract_tables_with_context(text):
            for row in table:
                if len(row) < 3:
                    continue
                model = _model_id(row[0])
                if not model or model in seen:
                    continue
                if not any(model.startswith(p) for p in _VALID_PREFIXES):
                    continue

                inp = _price(row[1])
                out = _price(row[2])
                if inp is None and out is None:
                    continue

                ctx = _context(row[3]) if len(row) > 3 else None
                seen.add(model)

                results.append(RawPrice(
                    provider=self.provider,
                    channel=self.channel,
                    model=model,
                    region=Region.INTL,
                    currency=Currency.USD,
                    input_per_1m=inp,
                    output_per_1m=out,
                    context_window=ctx,
                    source_url=self.source_url,
                ))

        # 如果表格解析为空,尝试从页面文本中用正则提取
        if not results:
            results = self._fallback_regex(text)
        return results

    def _fallback_regex(self, text: str) -> list[RawPrice]:
        """从页面 inner text 或 JSON-LD 中用正则提取价格。"""
        # 常见格式:模型名 … $X.XX/M input … $Y.YY/M output
        pat = re.compile(
            r"((?:mistral|codestral|pixtral|ministral|mixtral)[\w\- .]*?)"
            r"[\s\S]{0,200}?"
            r"\$\s*([\d.]+)\s*/?\s*M\s*(?:input|tokens?\s*input)"
            r"[\s\S]{0,100}?"
            r"\$\s*([\d.]+)\s*/?\s*M\s*(?:output|tokens?\s*output)",
            re.I,
        )
        results: list[RawPrice] = []
        for name, inp, out in pat.findall(text):
            model = _model_id(name)
            if model in {r.model for r in results}:
                continue
            results.append(RawPrice(
                provider=self.provider,
                channel=self.channel,
                model=model,
                region=Region.INTL,
                currency=Currency.USD,
                input_per_1m=float(inp),
                output_per_1m=float(out),
                source_url=self.source_url,
            ))
        return results
