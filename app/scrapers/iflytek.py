"""科大讯飞星火大模型官网定价抓取器 —— 官方 CNY per-token 价。

xinghuo.xfyun.cn/sparkapi 为 SPA，需 Playwright。
页面结构：多 tab（X2-Flash / X2/X1.5 / Ultra / Pro / Lite 等），
每个 tab 下有套餐包表格和按量价格卡片。

按量价格卡片格式（每个模型）：
    X2/X1.5模型
    2
    元/百万tokens
    ...

只抓 Spark 自研模型的按量刊例价，跳过套餐包和 MaaS 转售模型。
parse(text) 只吃渲染后文本，可离线 fixture 单测。
"""
from __future__ import annotations

import re

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

# Spark 模型名 → 规范化 model id
_SPARK_MODELS = {
    "X2-Flash":   "spark-x2-flash",
    "X2/X1.5":    "spark-x2",
    "Ultra":      "spark-ultra",
    "Pro":        "spark-pro",
    "Pro 128K":   "spark-pro-128k",
    "Max-批推理":  "spark-max-batch",
    "Lite":       "spark-lite",
}

# 按量价格卡片：「X2/X1.5模型\n2\n元/百万tokens」
_CARD = re.compile(
    r"(X2-Flash|X2/X1\.5|Ultra|Pro 128K|Pro|Max-批推理|Lite)\s*模型\s*\n\s*([\d.]+)\s*\n\s*元/百万tokens",
    re.I,
)


class IflytekScraper(BaseScraper):
    provider = "iflytek"
    channel = "official"
    source_url = "https://xinghuo.xfyun.cn/sparkapi"
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
        results: list[RawPrice] = []
        seen: set[str] = set()

        for m in _CARD.finditer(text):
            label = m.group(1).strip()
            price = float(m.group(2))
            model_id = _SPARK_MODELS.get(label)
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            results.append(RawPrice(
                provider=self.provider,
                channel=self.channel,
                model=model_id,
                region=Region.CN,
                currency=Currency.CNY,
                input_per_1m=price,
                output_per_1m=price,  # 讯飞统一价（输入输出同价）
                source_url=self.source_url,
            ))

        return results
