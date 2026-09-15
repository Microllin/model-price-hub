"""Cohere 官方定价抓取器 —— 官方 USD per-token 价。

来源:https://cohere.com/pricing 为 SSR 页面。
也可通过 https://api.cohere.com/v2/models 获取模型列表(无需 key),
但 API 不含价格,仍需网页。

页面结构:模型按档位分(Production/Default),每档一表:
  Model | Input ($/M tokens) | Output ($/M tokens)

Command 系列为核心文本模型。

降级链:HTML → 有头 Playwright
"""
from __future__ import annotations

import re
from html import unescape

from app.models.pricing import Currency, RawPrice, Region
from app.scrapers._html import extract_tables_with_context
from app.scrapers.base import BaseScraper, FetchLevel

_NUM = re.compile(r"\$?\s*([\d.]+)")


def _price(cell: str) -> float | None:
    m = _NUM.search(cell.replace(",", ""))
    return float(m.group(1)) if m else None


def _model_id(name: str) -> str:
    s = re.sub(r"\([^)]*\)", "", name).strip().lower()
    return re.sub(r"\s+", "-", s)


class CohereScraper(BaseScraper):
    provider = "cohere"
    channel = "official"
    source_url = "https://cohere.com/pricing"
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
                # Cohere 文本模型前缀
                if not any(model.startswith(p) for p in (
                    "command", "c4ai", "aya", "coral",
                )):
                    continue

                inp = _price(row[1])
                out = _price(row[2])
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

        # 当前官网把核心价格放在 HTML 内嵌的富文本列表中，不是 table。
        # 先抽取 "X pricing is $Y/1M tokens for input ... output" 句式。
        if not results:
            results = self._fallback_regex(text)
        return results

    def _fallback_regex(self, text: str) -> list[RawPrice]:
        plain = unescape(re.sub(r"<[^>]+>", " ", text))
        pat = re.compile(
            r"\b([A-Za-z][A-Za-z0-9+ -]*?)\s+pricing\s+is\s+"
            r"\$\s*([\d.]+)\s*/\s*1M\s+tokens?\s+for\s+input\s+and\s+"
            r"\$\s*([\d.]+)\s*/\s*1M\s+tokens?\s+for\s+output",
            re.I,
        )
        results: list[RawPrice] = []
        seen: set[str] = set()
        for name, inp, out in pat.findall(plain):
            # 只保留模型/产品名，避免把前文说明吞进名称。
            name = re.sub(r"^(?:the|our)\s+", "", name.strip(), flags=re.I)
            model = _model_id(name)
            if model in seen or not model.startswith(("command", "c4ai", "aya", "coral")):
                continue
            seen.add(model)
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
