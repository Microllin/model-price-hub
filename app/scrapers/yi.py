"""零一万物 (01AI/Yi) 官网定价抓取器 —— 官方 CNY per-token 价。

platform.lingyiwanwu.com/docs 为 SPA，需 Playwright。
定价表格为简单的 Markdown 表：
    模型	上下文长度	特性	场景	价格/1M token
    yi-lightning	16K	...	¥0.99
    yi-vision-v2	16K	...	¥6

parse(text) 只吃渲染后文本，可离线 fixture 单测。
"""
from __future__ import annotations

import re

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

# 模型名行：yi-开头
_YI_MODEL = re.compile(r"^(yi-[\w.\-]+)$", re.I)
# 价格：¥0.99 / ¥6
_YEN = re.compile(r"¥([\d.]+)")
# 上下文：16K / 200K
_CTX = re.compile(r"(\d+)\s*([KkMm])")


class YiScraper(BaseScraper):
    provider = "01ai"
    channel = "official"
    source_url = "https://platform.lingyiwanwu.com/docs"
    requires_render = True

    async def fetch(self) -> list[RawPrice]:
        if not settings.use_playwright:
            return []
        text = await self._render_text(self.source_url)
        return self.parse(text)

    async def _render_text(self, url: str) -> str:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            try:
                page = await browser.new_page(user_agent=settings.user_agent)
                await self._prepare_page(page)
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(5000)
                for _ in range(6):
                    await page.mouse.wheel(0, 15000)
                    await page.wait_for_timeout(500)
                return await page.inner_text("body")
            finally:
                await browser.close()

    def parse(self, text: str) -> list[RawPrice]:
        results: dict[str, RawPrice] = {}

        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("yi-"):
                continue
            # Tab-separated or whitespace-separated
            cells = [c.strip() for c in line.split("\t")] if "\t" in line else [line]
            model = cells[0]
            if model in results:
                continue

            # Find price and context in remaining cells or following lines
            price = None
            ctx_win = None
            joined = " ".join(cells)
            pm = _YEN.search(joined)
            if pm:
                price = float(pm.group(1))
            cm = _CTX.search(joined)
            if cm:
                val = int(cm.group(1))
                unit = cm.group(2).upper()
                ctx_win = val * 1000 if unit == "K" else val * 1_000_000

            if price is not None:
                results[model] = RawPrice(
                    provider=self.provider,
                    channel=self.channel,
                    model=model,
                    region=Region.CN,
                    currency=Currency.CNY,
                    input_per_1m=price,
                    output_per_1m=price,  # Yi 统一价
                    context_window=ctx_win,
                    source_url=self.source_url,
                )

        return list(results.values())
