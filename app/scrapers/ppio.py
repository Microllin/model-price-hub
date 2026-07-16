"""PPIO 派欧云抓取器 —— 又一批国内模型的真实 CNY per-token 价。

云托管平台,定价页 JS 渲染,需 Playwright。渲染后文本形如:
    GLM-4.7  ￥4/Mt  输入  ￥16/Mt  ...  上下文  ￥0.8/Mt  Cache Read ...
即「模型名 → ￥输入/Mt → 输入 → ￥输出/Mt」。模型名无 vendor 前缀,按关键词推断厂商。

与 SiliconFlow 覆盖有重叠(如 MiniMax-M2 两处均 ￥2.1/8.4),多一个源即多一层交叉印证。
parse(text) 只吃渲染后文本,可离线 fixture 单测。
"""
from __future__ import annotations

import re

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

# 模型名关键词(小写) → 本项目 provider
_KEYWORD_VENDOR = [
    ("glm", "zhipu"), ("deepseek", "deepseek"), ("kimi", "moonshot"),
    ("minimax", "minimax"), ("qwen", "aliyun"), ("doubao", "bytedance"),
    ("hunyuan", "tencent"), ("ernie", "baidu"), ("baichuan", "baichuan"),
    ("longcat", "meituan"), ("step", "stepfun"), ("llama", "meta"),
]

# 「模型名 → ￥输入/Mt → 输入 → ￥输出/Mt」;模型名为英文起头、可含空格/点/横杠
_PAT = re.compile(
    r"([A-Za-z][\w.\- ]{1,40}?)\s*"
    r"￥([\d.]+)\s*/\s*Mt\s*"
    r"输入\s*"
    r"￥([\d.]+)\s*/\s*Mt"
)

# 误当成模型名的标签
_NAME_BLACKLIST = {"cache read", "输入", "输出", "上下文", "hot", "new"}


class PPIOScraper(BaseScraper):
    provider = "ppio"
    channel = "ppio"
    source_url = "https://ppio.com/model-api/product/llm-api"
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
                await page.wait_for_timeout(3000)
                for _ in range(12):
                    await page.mouse.wheel(0, 20000)
                    await page.wait_for_timeout(800)
                return await page.inner_text("body")
            finally:
                await browser.close()

    def parse(self, text: str) -> list[RawPrice]:
        results: dict[str, RawPrice] = {}
        for name, inp, out in _PAT.findall(text):
            name = name.strip()
            if name.lower() in _NAME_BLACKLIST or len(name) < 2:
                continue
            input_p, output_p = float(inp), float(out)
            if input_p == 0 and output_p == 0:
                continue
            results[name] = RawPrice(
                provider=self._vendor(name),
                channel=self.channel,
                model=name,
                region=Region.CN,
                currency=Currency.CNY,
                input_per_1m=input_p,
                output_per_1m=output_p,
                source_url=self.source_url,
            )
        return list(results.values())

    @staticmethod
    def _vendor(name: str) -> str:
        low = name.lower()
        for kw, vendor in _KEYWORD_VENDOR:
            if kw in low:
                return vendor
        return "ppio"  # 未知厂商归到平台名
