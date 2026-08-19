"""火山引擎（字节跳动）官方定价抓取器 —— 官方 CNY per-token 价。

https://www.volcengine.com/docs/82379/1099320 为 SPA，需 Playwright 渲染。
渲染后含 26+ 个 HTML table，按表前关键词识别三种 per-token 定价表：

- 在线推理（常规）→ service_tier=standard
- 在线推理（低延迟）→ service_tier=low_latency
- 批量推理 → service_tier=batch

表结构：模型名称 | 条件（千token）| 输入(非音频) | 输入(音频) | [缓存存储] |
缓存命中(非音频) | 缓存命中(音频) | 输出（单位：元/百万token）

同一模型的上下文分档（如 [0,32] / (32,128]）拆成多条 context_range 记录。
模型名只在首行出现，后续分档行模型名为空。

视频（seedance）/ 图片（seedream）/ 3D / embedding / 精调 / TPM 保障包 / 知识库等
非 per-token 表跳过。
"""
from __future__ import annotations

import re

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers._html import extract_tables
from app.scrapers.base import BaseScraper

# 表头列名 → 字段映射（部分匹配）
_COL_MAP = {
    "输入(非音频)": "input_per_1m",
    "输入（非音频）": "input_per_1m",
    "输出": "output_per_1m",
    "缓存命中(非音频)": "cached_input_per_1m",
    "缓存命中（非音频）": "cached_input_per_1m",
}

_NUM = re.compile(r"^[\d.]+$")
_TIER = re.compile(r"\[?\(?\s*(\d+)\s*[,，]\s*(\d+)\s*[)\]]\s*$")
_TIER_OPEN = re.compile(r"\[?\(?\s*(\d+)\s*[,，]\s*(\d+)\s*\]")
_MODEL = re.compile(r"^doubao-[\w.\-]+$", re.I)

# 关键词 → service_tier
_TIER_KEYWORDS = {
    "在线推理（常规）": "standard",
    "在线推理（低延迟）": "low_latency",
    "批量推理": "batch",
}


def _parse_price(cell: str) -> float | None:
    """'6.00​' / '-​' → float | None。去除 zero-width joiner。"""
    s = cell.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").strip()
    if not s or s == "-":
        return None
    m = _NUM.match(s)
    return float(m.group()) if m else None


def _parse_context_range(cell: str) -> str | None:
    """'输入长度 [0, 1024]​' → '0-1024k'; '输入长度 (32, 128]​' → '32k-128k'。"""
    s = cell.replace("\u200b", "").strip()
    m = _TIER_OPEN.search(s) or _TIER.search(s)
    if not m:
        return None
    lo, hi = int(m.group(1)), int(m.group(2))
    return f"{lo}k-{hi}k" if lo > 0 else f"0-{hi}k"


class VolcEngineScraper(BaseScraper):
    provider = "bytedance"
    channel = "official"
    source_url = "https://www.volcengine.com/docs/82379/1099320"
    requires_render = True

    async def fetch(self) -> list[RawPrice]:
        if not settings.use_playwright:
            return []
        html = await self._render_html(self.source_url)
        return self.parse(html)

    async def _render_html(self, url: str) -> str:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            try:
                page = await browser.new_page(user_agent=settings.user_agent)
                await self._prepare_page(page)
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(5000)
                for _ in range(8):
                    await page.mouse.wheel(0, 3000)
                    await page.wait_for_timeout(600)
                return await page.content()
            finally:
                await browser.close()

    def parse(self, html: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        tables = extract_tables(html)

        # 用表前文本关键词识别 service_tier
        for i, table in enumerate(tables):
            tier = self._detect_tier(html, table, i)
            if tier is None:
                continue
            col_map = self._map_columns(table[0] if table else [])
            if "input_per_1m" not in col_map.values() or "output_per_1m" not in col_map.values():
                continue  # 非 per-token 表
            results.extend(self._parse_table(table, col_map, tier))

        return results

    def _detect_tier(self, html: str, table: list[list[str]], index: int) -> str | None:
        """从表前 HTML 文本中寻找 service_tier 关键词。"""
        # 在 HTML 中找到这个表的位置，回溯 2000 字符找关键词
        flat_header = " ".join(table[0]) if table else ""
        if "模型名称" not in flat_header and "模型" not in flat_header:
            return None  # 非模型定价表
        # 检查是否是 per-token 定价表（含"元/百万token"）
        all_text = " ".join(" ".join(r) for r in table).lower()
        if "token" not in all_text and "元" not in all_text:
            return None
        if "tpm" in all_text or "元/个" in all_text or "元/次" in all_text or "元/秒" in all_text:
            return None  # TPM/per-次/per-秒 表

        # 在 html 源码中定位此表，回溯找关键词
        # 用表头首格文本定位
        search_start = 0
        for _ in range(index + 1):
            pos = html.find("<table", search_start)
            if pos < 0:
                break
            search_start = pos + 1

        before = html[max(0, search_start - 3000):search_start]
        before_text = re.sub(r"<[^>]+>", " ", before)
        # 取最后出现的关键词(离表格最近的那个)
        best_tier, best_pos = "standard", -1
        for kw, tier in _TIER_KEYWORDS.items():
            idx = before_text.rfind(kw)
            if idx > best_pos:
                best_tier, best_pos = tier, idx
        return best_tier if best_pos >= 0 else "standard"

    @staticmethod
    def _map_columns(header: list[str]) -> dict[int, str]:
        """表头列名 → 字段映射（索引 → 字段名）。"""
        col_map = {}
        for i, cell in enumerate(header):
            clean = cell.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").strip()
            for pattern, field in _COL_MAP.items():
                if pattern in clean:
                    col_map[i] = field
                    break
            if "条件" in clean:
                col_map[i] = "condition"
            if "模型" in clean and "名称" in clean:
                col_map[i] = "model"
            elif i == 0 and "模型" in clean:
                col_map[i] = "model"
        return col_map

    def _parse_table(self, table: list[list[str]], col_map: dict[int, str], tier: str) -> list[RawPrice]:
        results = []
        current_model = ""

        for row in table[1:]:  # 跳过表头
            if len(row) < 3:
                continue

            # 模型名：首行有名字，后续分档行为空
            model_idx = next((i for i, f in col_map.items() if f == "model"), 0)
            model_cell = row[model_idx].replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").strip() if model_idx < len(row) else ""
            if model_cell and _MODEL.match(model_cell):
                current_model = model_cell
            if not current_model:
                continue

            # 上下文分档
            cond_idx = next((i for i, f in col_map.items() if f == "condition"), None)
            context_range = _parse_context_range(row[cond_idx]) if cond_idx is not None and cond_idx < len(row) else None

            # 价格
            prices = {}
            for i, field in col_map.items():
                if field in ("model", "condition"):
                    continue
                if i < len(row):
                    prices[field] = _parse_price(row[i])

            if not any(v is not None for v in prices.values()):
                continue

            results.append(RawPrice(
                provider=self.provider,
                channel=self.channel,
                model=current_model,
                region=Region.CN,
                currency=Currency.CNY,
                input_per_1m=prices.get("input_per_1m"),
                output_per_1m=prices.get("output_per_1m"),
                cached_input_per_1m=prices.get("cached_input_per_1m"),
                context_range=context_range,
                service_tier=tier,
                source_url=self.source_url,
            ))

        return results
