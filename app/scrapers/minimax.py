"""MiniMax 开放平台官网定价抓取器 —— 官方 CNY per-token 价。

platform.minimaxi.com/docs/guides/pricing-paygo 为 SPA,需 Playwright。渲染后为
制表符分隔的表格,文本模型行形如(单位:元/百万 tokens):
    MiniMax-M3   ≤512k 输入 tokens 永久五折   4.20 2.10   16.80 8.40   0.84 0.42
    MiniMax-M3   >512k 输入 tokens 永久五折   8.40 4.20    33.60 16.80  1.68 0.84
    MiniMax-M2.7   2.1   8.4   0.42   2.625
折扣行含「原价 折后价」两个数,取折后价(最后一个);上下文阶梯(≤512k / >512k)
拆成多条带 context_range 的记录。视频(Hailuo)/音乐/语音等非 per-token 行跳过。
parse(text) 只吃渲染后文本,可离线 fixture 单测。
"""
from __future__ import annotations

import re

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

_PRICE_CELL = re.compile(r"^[\d.\s]+$")
_SKIP = ("视频", "图生", "文生", "音乐", "语音", "图片", "hailuo", "speech", "voice")
# 阶梯/促销标签格:"≤ 512k 输入 tokens 永久五折" / "> 512k 输入 tokens"
_TIER_LABEL = re.compile(r"(≤|>=?|＞)\s*(\d+(?:\.\d+)?\s*[KkMm])")


def _tier_of(cell: str) -> str | None:
    """从标签格提取上下文档位:'≤ 512k …' → '0-512k';'> 512k …' → '>512k'。"""
    m = _TIER_LABEL.search(cell)
    if not m:
        return None
    raw = m.group(2).replace(" ", "")
    n = float(re.match(r"[\d.]+", raw).group())
    if raw.lower().endswith("m"):
        n *= 1000
    bound = f"{int(n)}k" if n == int(n) else f"{n}k"
    return f">{bound}" if m.group(1).startswith((">", "＞")) else f"0-{bound}"


class MiniMaxScraper(BaseScraper):
    provider = "minimax"
    channel = "official"
    source_url = "https://platform.minimaxi.com/docs/guides/pricing-paygo"
    requires_render = True

    async def fetch(self) -> list[RawPrice]:
        if not settings.use_playwright:
            return []
        text = await self._render_text(self.source_url)
        return self.parse(text)

    async def _render_text(self, url: str) -> str:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
            try:
                page = await browser.new_page(user_agent=settings.user_agent)
                await self._prepare_page(page)
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3500)
                for _ in range(6):
                    await page.mouse.wheel(0, 15000)
                    await page.wait_for_timeout(600)
                return await page.inner_text("body")
            finally:
                await browser.close()

    def parse(self, text: str) -> list[RawPrice]:
        results: dict[tuple[str, str | None], RawPrice] = {}
        for line in text.split("\n"):
            if not line.strip().startswith("MiniMax-"):
                continue
            low = line.lower()
            if any(k in low for k in _SKIP):
                continue
            cells = [c.strip() for c in line.split("\t")]
            name = cells[0].strip()
            # 第二格可能是阶梯/促销标签(含 tokens/折),也可能是价格
            tier = _tier_of(cells[1]) if len(cells) > 1 else None
            if len(cells) > 1 and tier is None and not _PRICE_CELL.match(cells[1]):
                continue  # 无法识别的描述行
            key = (name, tier)
            if key in results:
                continue
            price_cells = [c for c in cells[1:] if _PRICE_CELL.match(c) and re.search(r"\d", c)]
            if len(price_cells) < 2:
                continue
            # 折扣行取最后一个数字(折后价)
            inp = float(re.findall(r"[\d.]+", price_cells[0])[-1])
            out = float(re.findall(r"[\d.]+", price_cells[1])[-1])
            cache = (
                float(re.findall(r"[\d.]+", price_cells[2])[-1])
                if len(price_cells) >= 3
                else None
            )
            if inp == 0 and out == 0:
                continue
            results[key] = RawPrice(
                provider=self.provider,
                channel=self.channel,
                model=name,
                region=Region.CN,
                currency=Currency.CNY,
                input_per_1m=inp,
                output_per_1m=out,
                cached_input_per_1m=cache,
                context_range=tier,
                source_url=self.source_url,
            )
        return list(results.values())
