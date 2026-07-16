"""腾讯混元官网定价抓取器 —— 官方 CNY per-token 价。

cloud.tencent.com/document/product/1729/97731(混元生文计费概述)为 SPA,需 Playwright。
渲染后刊例价(每百万 tokens)形如:
    Hunyuan-a13b   输入：0.5元   输出：2元
取每模型首次出现的输入/输出价。排除 vision/embedding 等非纯文本模型。
parse(text) 只吃渲染后文本,可离线 fixture 单测。
"""
from __future__ import annotations

import re

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

_PAT = re.compile(
    r"(Hunyuan[-\w.]+)"
    r"[\s\S]{0,80}?输入[：:]\s*([\d.]+)\s*元"
    r"[\s\S]{0,24}?输出[：:]\s*([\d.]+)\s*元"
)
_SKIP = ("vision", "embedding", "image", "video", "ocr", "3d", "-mt", "translation")


class TencentScraper(BaseScraper):
    provider = "tencent"
    channel = "official"
    source_url = "https://cloud.tencent.com/document/product/1729/97731"
    requires_render = True

    async def fetch(self) -> list[RawPrice]:
        if not settings.use_playwright:
            return []
        text = await self._render_text(self.source_url)
        return self.parse(text)

    async def _render_text(self, url: str) -> str:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = await browser.new_page(user_agent=settings.user_agent)
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(5000)
                for _ in range(6):
                    await page.mouse.wheel(0, 15000)
                    await page.wait_for_timeout(600)
                return await page.inner_text("body")
            finally:
                await browser.close()

    def parse(self, text: str) -> list[RawPrice]:
        results: dict[str, RawPrice] = {}
        for name, inp, out in _PAT.findall(text):
            low = name.lower()
            if any(k in low for k in _SKIP) or name in results:
                continue
            input_p, output_p = float(inp), float(out)
            if input_p == 0 and output_p == 0:
                continue
            results[name] = RawPrice(
                provider=self.provider,
                channel=self.channel,
                model=name,
                region=Region.CN,
                currency=Currency.CNY,
                input_per_1m=input_p,
                output_per_1m=output_p,
                source_url=self.source_url,
            )
        return list(results.values())
