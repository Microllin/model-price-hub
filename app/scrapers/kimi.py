"""月之暗面 Kimi 官网定价抓取器 —— HTTP 直取 .md 文件解析。

Kimi 文档站 (platform.kimi.com/docs) 使用 Docusaurus，
每个定价子页提供 .md 源文件，内含 DocTable 组件：
    rows={[
        ["kimi-k3", "1M tokens", "¥2.00", "¥20.00", "¥100.00", "1,048,576 tokens"],
    ]}
通过 HTTP GET 直取 .md 文件，用正则提取 rows 数据，无需 Playwright/视觉 OCR。

覆盖页面：
- chat-k3.md      Kimi K3 旗舰
- chat-k27-code.md K2.7 Code + HighSpeed
- chat-k26.md     K2.6
- chat-k25.md     K2.5
- chat-v1.md      Moonshot V1 系列
- batch.md        批量推理
- tools.md        联网搜索工具
"""
from __future__ import annotations

import re

from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

_BASE = "https://platform.kimi.com/docs/pricing"
_MD_PAGES = [
    f"{_BASE}/chat-k3.md",
    f"{_BASE}/chat-k27-code.md",
    f"{_BASE}/chat-k26.md",
    f"{_BASE}/chat-k25.md",
    f"{_BASE}/chat-v1.md",
    f"{_BASE}/batch.md",
    f"{_BASE}/tools.md",
]

# 从 rows={[...]} 提取每行 JSON 数组
_ROW = re.compile(r'\["([^"]+)"[^]]*\]')
# 价格：¥2.00 / ￥0.03
_YEN = re.compile(r"[¥￥]([\d.]+)")
# 上下文窗口：1,048,576 tokens / 262,144 tokens
_CTX_WIN = re.compile(r"([\d,]+)\s*tokens")


class KimiScraper(BaseScraper):
    provider = "moonshot"
    channel = "official"
    source_url = f"{_BASE}/chat"

    async def urls(self) -> list[str]:
        return _MD_PAGES

    def parse(self, text: str) -> list[RawPrice]:
        """解析单个 .md 文件的 DocTable rows。"""
        results: list[RawPrice] = []

        # 判断页面类型（用标题行判断，不用全文搜索以避免误匹配）
        first_lines = text[:500]
        is_batch = "批量推理定价" in first_lines
        is_tool = "联网搜索定价" in first_lines
        is_v1 = "Moonshot V1 定价" in first_lines

        # 提取所有 rows 块
        rows_match = re.search(r"rows=\{?\[(.*?)\]\}?\s*/?>", text, re.S)
        if not rows_match:
            return []

        rows_block = rows_match.group(1)
        # 提取每个 [...] 行
        for row_match in re.finditer(r'\[([^\[\]]+)\]', rows_block):
            cells_raw = row_match.group(1)
            # 提取所有引号字符串
            cells = re.findall(r'"([^"]*)"', cells_raw)
            if not cells:
                continue

            if is_tool:
                # 联网搜索：["联网搜索", "1 次", "￥0.03", ...]
                price_m = _YEN.search(cells_raw)
                if price_m:
                    results.append(RawPrice(
                        provider=self.provider, channel=self.channel,
                        model="web_search", region=Region.CN, currency=Currency.CNY,
                        input_per_1m=float(price_m.group(1)), output_per_1m=None,
                        billing_unit="request", modality="search",
                        source_url=self.source_url,
                    ))
                continue

            # 模型定价行：["model", "1M tokens", "¥cached", "¥input", "¥output", "ctx tokens"]
            model_name = cells[0].strip()
            # 去掉 "（Batch）" 后缀用于标识
            clean_name = re.sub(r"\s*（.*?）\s*", "", model_name)

            prices = [_YEN.search(c) for c in cells[1:]]
            # 只保留有 ¥ 价格的值，跳过无价格的单元格
            price_vals = [float(m.group(1)) for m in prices if m]

            # 提取上下文窗口
            ctx_win = None
            for c in cells:
                cm = _CTX_WIN.search(c)
                if cm:
                    ctx_win = int(cm.group(1).replace(",", ""))

            if is_v1:
                # V1 格式：["model", "1M tokens", "¥input", "¥output", "ctx"]
                # 无缓存价格
                inp = price_vals[0] if len(price_vals) > 0 else None
                out = price_vals[1] if len(price_vals) > 1 else None
                if inp is not None:
                    results.append(RawPrice(
                        provider=self.provider, channel=self.channel,
                        model=model_name, region=Region.CN, currency=Currency.CNY,
                        input_per_1m=inp, output_per_1m=out,
                        context_window=ctx_win,
                        service_tier="batch" if is_batch else "standard",
                        source_url=self.source_url,
                    ))
            else:
                # K3/K2.7/K2.6/K2.5/Batch 格式：
                # ["model", "1M tokens", "¥cached", "¥input", "¥output", "ctx"]
                cached = price_vals[0] if len(price_vals) > 0 else None
                inp = price_vals[1] if len(price_vals) > 1 else None
                out = price_vals[2] if len(price_vals) > 2 else None
                if inp is not None:
                    results.append(RawPrice(
                        provider=self.provider, channel=self.channel,
                        model=model_name, region=Region.CN, currency=Currency.CNY,
                        input_per_1m=inp, output_per_1m=out,
                        cached_input_per_1m=cached,
                        context_window=ctx_win,
                        service_tier="batch" if is_batch else "standard",
                        source_url=self.source_url,
                    ))

        return results
