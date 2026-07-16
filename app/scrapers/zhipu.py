"""智谱 GLM 官网定价抓取器 —— 官方 CNY per-token 价。

open.bigmodel.cn/pricing 为 SPA,需 Playwright。渲染后 GLM 模型按上下文长度分档,
单位为元/百万tokens(= 每 1M):
    GLM-5.1  输入长度[0,32) 6元 24元  限时免费 1.3元 ...  输入长度[32+) 8元 28元
取每个模型第一档的输入/输出价作为官方价(channel=official → 参与置信度)。
parse(text) 只吃渲染后文本,可离线 fixture 单测。
"""
from __future__ import annotations

import re

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

# 「GLM 模型名 … X元 Y元」——取模型名后的头两个价格(输入/输出,元/百万tokens)。
# 不依赖"输入长度"字样:新旗舰用"新品 1M"格式(如 GLM-5.2 新品 1M 8元 28元),
# 老模型用"输入长度[..]"格式(如 GLM-5.1 输入长度[0,32) 6元 24元),两者都覆盖。
_PAT = re.compile(
    r"(GLM-?[0-9][\w.\-]*)"        # 模型名:GLM-5.2 / GLM-5.1 / GLM-5-Turbo / GLM-5
    r"[\s\S]{0,60}?([\d.]+)\s*元"   # 首个价格(输入),跳过 新品/1M/输入长度[..] 等
    r"[\s\S]{0,20}?([\d.]+)\s*元"   # 次个价格(输出)
)


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
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = await browser.new_page(user_agent=settings.user_agent)
                await page.goto(url, wait_until="networkidle", timeout=45000)
                await page.wait_for_timeout(4000)
                for _ in range(6):
                    await page.mouse.wheel(0, 15000)
                    await page.wait_for_timeout(700)
                return await page.inner_text("body")
            finally:
                await browser.close()

    def parse(self, text: str) -> list[RawPrice]:
        results: dict[str, RawPrice] = {}
        for name, inp, out in _PAT.findall(text):
            name = name.strip()
            if name in results:  # 只取每个模型第一档
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
