"""智谱 GLM 官网定价抓取器 —— 官方 CNY per-token 价。

open.bigmodel.cn/pricing 为 SPA,需 Playwright。渲染后 GLM 模型按上下文长度分档,
单位为元/百万tokens(= 每 1M),行序列形如:
    GLM-5.1 / 输入长度 [0, 32) / 6元 / 24元 / 限时免费 / 1.3元 / 输入长度 [32+) / 8元 / 28元
每个阶梯产出一条带 context_range 的记录;「限时免费」等促销行只取首两个正式价。
无阶梯的新品(如 GLM-5.2 新品 1M 8元 28元)产出单条 context_range=None 记录。
parse(text) 只吃渲染后文本,可离线 fixture 单测。
"""
from __future__ import annotations

import re

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

# 模型名行:GLM-5.2 / GLM-5.1 / GLM-5-Turbo / GLM-5
_NAME = re.compile(r"^(GLM-?[0-9][\w.\-]*)$", re.I)
# 阶梯行:「输入长度 [0, 32)」或「输入长度 [32+)」(单位 K)
_TIER = re.compile(r"输入长度\s*\[\s*(\d+)\s*(?:[,，]\s*(\d+)\s*|\+\s*)\)")
_PRICE = re.compile(r"^([\d.]+)\s*元")


def _norm_tier(m: re.Match) -> str:
    lo, hi = m.group(1), m.group(2)
    return f"{lo}-{hi}k" if hi else f">{lo}k"


class ZhipuScraper(BaseScraper):
    provider = "zhipu"
    channel = "official"
    source_url = "https://open.bigmodel.cn/pricing"
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
                await page.wait_for_timeout(4000)
                for _ in range(6):
                    await page.mouse.wheel(0, 15000)
                    await page.wait_for_timeout(700)
                return await page.inner_text("body")
            finally:
                await browser.close()

    def parse(self, text: str) -> list[RawPrice]:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        results: dict[tuple[str, str | None], RawPrice] = {}

        def emit(name: str, tier: str | None, prices: list[float]) -> None:
            if len(prices) < 2 or (prices[0] == 0 and prices[1] == 0):
                return
            key = (name, tier)
            if key in results:
                return
            results[key] = RawPrice(
                provider=self.provider,
                channel=self.channel,
                model=name,
                region=Region.CN,
                currency=Currency.CNY,
                input_per_1m=prices[0],
                output_per_1m=prices[1],
                context_range=tier,
                source_url=self.source_url,
            )

        for i, line in enumerate(lines):
            m = _NAME.match(line)
            if not m:
                continue
            name = m.group(1)
            tier: str | None = None
            prices: list[float] = []
            for nxt in lines[i + 1 : i + 25]:
                if _NAME.match(nxt):
                    break
                tm = _TIER.search(nxt)
                if tm:
                    emit(name, tier, prices)
                    tier, prices = _norm_tier(tm), []
                    continue
                pm = _PRICE.match(nxt)
                if pm and len(prices) < 2:  # 促销价(限时免费等)不覆盖正式价
                    prices.append(float(pm.group(1)))
            emit(name, tier, prices)
        return list(results.values())
