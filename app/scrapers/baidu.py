"""百度文心(千帆)官网定价抓取器 —— 官方 CNY per-token 价。

cloud.baidu.com/doc/qianfan/s/wmh4sv6ya 为 SPA,需 Playwright。渲染后为制表符分隔表格:
    ERNIE 5.1   ERNIE-5.1   推理服务   输入（输入<=32k）   0.004   -   元/千tokens
    输出（输入<=32k）   0.018   -
单位是「元/千tokens」→ ×1000 归一到每 1M。取每模型首档(<=32k)输入/输出。
parse(text) 只吃渲染后文本,可离线 fixture 单测。
"""
from __future__ import annotations

import re

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

# 百度页面混排价格格式。正则只可靠抓【有明确"输入/输出"标签】的:
#  A) 新旗舰 tab 分隔:ERNIE-5.1  推理服务  输入…  0.004  -  元/千tokens
_PAT_TAB = re.compile(
    r"(ERNIE[-\w.]+)\t推理服务\t输入[^\t]*\t([\d.]+)"
    r"[\s\S]{0,80}?输出[^\t]*\t([\d.]+)"
)
#  B) 内联带标签:ERNIE-Speed-8K 输入:0.012元/千tokens 输出:0.024元/千tokens
#     (Lite 等无"输入/输出"标签、只有两个裸数字的,列序不确定 → 不猜,交视觉主源)
_PAT_INLINE = re.compile(
    r"(ERNIE[-\w.]+)\s+输入[：:]\s*([\d.]+)\s*元/千tokens"
    r"\s+输出[：:]\s*([\d.]+)\s*元/千tokens"
)
_SKIP = ("vl", "ocr", "image", "-vision", "embedding", "rerank", "tokenizer")


class BaiduScraper(BaseScraper):
    provider = "baidu"
    channel = "official"
    source_url = "https://cloud.baidu.com/doc/qianfan/s/wmh4sv6ya"
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
        # 两种格式都扫;tab 格式优先(新旗舰),内联补 Lite/Speed
        for name, inp, out in list(_PAT_TAB.findall(text)) + list(_PAT_INLINE.findall(text)):
            low = name.lower()
            if any(k in low for k in _SKIP) or name in results:
                continue
            # 元/千tokens → 元/百万tokens
            input_p = round(float(inp) * 1000, 4)
            output_p = round(float(out) * 1000, 4)
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
