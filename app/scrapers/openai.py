"""OpenAI 官方定价抓取器 —— 官方 USD per-token 价。

https://developers.openai.com/api/docs/pricing 为 Astro SSR 页面,表格直接在 HTML
中,plain HTTP 即可。页面结构:

- 文本模型按服务档位拆成 4 个 astro-island,props 里带 tier:
  standard / batch(5 折) / flex / fast(原 Priority,2026-07-30 更名)
- 每表 9 列:Model | Input | Cached input | Cache writes | Output | (长上下文同 4 列)
  短上下文为默认价;长上下文列产出 context_range="long" 的条件价
  (阈值因模型而异,如 gpt-5.5 为 272K,页面未给统一值,故用 "long" 标记)
- Realtime 等分组表带 Modality 列 → 按模态产出(audio/text/image)
- 图像生成(每张)、转写(每分钟)等非 token 计价表跳过

模型 id 页面原文即是 litellm 风格(gpt-5.6-sol),直接与旁证交叉验证。
"""
from __future__ import annotations

import re

from app.models.pricing import Currency, RawPrice, Region
from app.scrapers._html import extract_tables
from app.scrapers.base import BaseScraper

_NUM = re.compile(r"[-+]?\d*\.?\d+")
_TIER = re.compile(r"&quot;tier&quot;:\[0,&quot;(\w+)&quot;\]")
# 每个 astro-island 一个组件;表格在 island 内部
_ISLAND = re.compile(r"<astro-island\b")


def _price(cell: str) -> float | None:
    """'$5.00' → 5.0;'-'/空 → None。"""
    m = _NUM.search(cell.replace(",", ""))
    return float(m.group()) if m else None


def _model_id(cell: str) -> str:
    """'gpt-5.5 (<272K context length)' → 'gpt-5.5'。"""
    return re.sub(r"\([^)]*\)", "", cell).strip().lower()


class OpenAIScraper(BaseScraper):
    provider = "openai"
    channel = "official"
    source_url = "https://developers.openai.com/api/docs/pricing"

    async def urls(self) -> list[str]:
        return [self.source_url]

    def parse(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        # 按 astro-island 切分,tier 从 island props 提取(缺省 standard)
        chunks = _ISLAND.split(text)
        for chunk in chunks[1:]:
            if "<table" not in chunk:
                continue
            end = chunk.find("</astro-island>")
            island = chunk[:end] if end > 0 else chunk
            m = _TIER.search(island)
            tier = m.group(1) if m else "standard"
            for table in extract_tables(island):
                results.extend(self._table(table, tier))
        return results

    def _row(self, model: str, **kw) -> RawPrice:
        return RawPrice(
            provider=self.provider, channel=self.channel, model=model,
            region=Region.INTL, currency=Currency.USD, source_url=self.source_url, **kw
        )

    def _table(self, table: list[list[str]], tier: str) -> list[RawPrice]:
        if not table:
            return []
        header = [c.strip().lower() for c in table[0]] + [c.strip().lower() for c in (table[1] if len(table) > 1 else [])]
        if "short context" in header or ("input" in header and "cache writes" in header):
            return self._token_table(table, tier)
        if "modality" in header:
            return self._modality_table(table, tier)
        if "category" in header and "cached input" in header:
            return self._category_table(table, tier)
        return []  # 图像/转写/工具/微调训练等非 token 表

    def _token_table(self, table: list[list[str]], tier: str) -> list[RawPrice]:
        """9 列长短上下文表:短档为默认价,长档 context_range='long'。"""
        out: list[RawPrice] = []
        for row in table:
            if len(row) < 5 or not row[0].strip() or row[0].strip().lower() == "model":
                continue
            model = _model_id(row[0])
            if not model:
                continue
            vals = [_price(c) for c in row[1:]]
            vals += [None] * (8 - len(vals))
            si, sc, scw, so, li, lc, lcw, lo = vals[:8]
            if si is None and so is None:
                continue
            out.append(self._row(model, input_per_1m=si, output_per_1m=so,
                                 cached_input_per_1m=sc, cache_write_per_1m=scw,
                                 service_tier=tier))
            if li is not None or lo is not None:
                out.append(self._row(model, input_per_1m=li, output_per_1m=lo,
                                     cached_input_per_1m=lc, cache_write_per_1m=lcw,
                                     service_tier=tier, context_range="long"))
        return out

    def _category_table(self, table: list[list[str]], tier: str) -> list[RawPrice]:
        """Category 表(ChatGPT/Codex 等):[分类, 模型, Input, Cached input, Output]。"""
        out: list[RawPrice] = []
        for row in table[1:]:
            if len(row) < 5:
                continue
            model = _model_id(row[1])
            inp, cached, outp = _price(row[2]), _price(row[3]), _price(row[4])
            if not model or (inp is None and outp is None):
                continue
            out.append(self._row(model, input_per_1m=inp, output_per_1m=outp,
                                 cached_input_per_1m=cached, service_tier=tier))
        return out

    def _modality_table(self, table: list[list[str]], tier: str) -> list[RawPrice]:
        """Realtime 分组表:模型名只在组首行,后续行首格为空;按 Modality 列拆模态。"""
        out: list[RawPrice] = []
        current = ""
        for row in table[1:]:
            if len(row) < 5:
                continue
            if row[0].strip():
                current = _model_id(row[0])
            if not current:
                continue
            modality = row[1].strip().lower()
            if modality not in ("audio", "text", "image", "video"):
                continue
            inp, cached, outp = _price(row[2]), _price(row[3]), _price(row[4])
            if inp is None and outp is None:
                continue
            out.append(self._row(current, input_per_1m=inp, output_per_1m=outp,
                                 cached_input_per_1m=cached, service_tier=tier,
                                 modality=modality))
        return out
