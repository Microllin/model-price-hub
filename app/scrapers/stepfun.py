"""阶跃星辰 (StepFun) 官方定价抓取器 —— 官方 CNY per-token 价。

https://platform.stepfun.com/docs/pricing/details 为 SPA,需 Playwright 渲染。
渲染后文本按模型分组,格式形如(单位:元/百万 tokens):
    Step-2    输入 4.0    输出 16.0
    Step-2-16K    输入 1.0    输出 4.0

降级链:有头 Playwright + 正则(零 token)
"""
from __future__ import annotations

import re

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper, FetchLevel

# StepFun 模型名
_NAME = re.compile(r"^(step-[\w.\-]+)$", re.I)
_PRICE = re.compile(r"([\d.]+)")
_SKIP = ("image", "video", "audio", "voice", "embedding", "vision", "tts")

# 制表符分隔:模型名 \t 输入价 \t 输出价
_PAT_TAB = re.compile(
    r"(step-[\w.\-]+)\t[^\t]*?([\d.]+)[^\t]*?\t[^\t]*?([\d.]+)",
    re.I,
)
# 内联:模型名 输入 X 输出 Y
_PAT_INLINE = re.compile(
    r"(step-[\w.\-]+)"
    r"[\s\S]{0,80}?输入[：:\s]*([\d.]+)"
    r"[\s\S]{0,80}?输出[：:\s]*([\d.]+)",
    re.I,
)


class StepFunScraper(BaseScraper):
    provider = "stepfun"
    channel = "official"
    source_url = "https://platform.stepfun.com/docs/pricing/details"
    requires_render = True
    fetch_chain = [FetchLevel.RENDER]

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
                await page.wait_for_timeout(4000)
                for _ in range(8):
                    await page.mouse.wheel(0, 15000)
                    await page.wait_for_timeout(800)
                return await page.inner_text("body")
            finally:
                await browser.close()

    def parse(self, text: str) -> list[RawPrice]:
        results: dict[str, RawPrice] = {}

        # 尝试制表符分隔格式
        for name, inp, out in _PAT_TAB.findall(text):
            self._add(results, name, inp, out)

        # 尝试内联格式
        for name, inp, out in _PAT_INLINE.findall(text):
            self._add(results, name, inp, out)

        # 逐行扫描:模型名行 → 后续价格行
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            m = _NAME.match(line)
            if not m:
                continue
            name = m.group(1)
            if name.lower() in results or any(k in name.lower() for k in _SKIP):
                continue
            # 向后扫描找输入/输出价格
            prices: list[float] = []
            for nxt in lines[i + 1 : i + 15]:
                if _NAME.match(nxt):
                    break
                for pm in _PRICE.finditer(nxt):
                    try:
                        prices.append(float(pm.group(1)))
                    except ValueError:
                        pass
                    if len(prices) >= 2:
                        break
                if len(prices) >= 2:
                    break
            if len(prices) >= 2:
                self._add(results, name, str(prices[0]), str(prices[1]))

        return list(results.values())

    def _add(self, results: dict[str, RawPrice], name: str, inp: str, out: str) -> None:
        low = name.lower()
        if low in results or any(k in low for k in _SKIP):
            return
        input_p, output_p = float(inp), float(out)
        if input_p == 0 and output_p == 0:
            return
        results[low] = RawPrice(
            provider=self.provider,
            channel=self.channel,
            model=name,
            region=Region.CN,
            currency=Currency.CNY,
            input_per_1m=input_p,
            output_per_1m=output_p,
            source_url=self.source_url,
        )
