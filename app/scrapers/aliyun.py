"""通义千问(阿里云百炼)官网定价抓取器 —— 官方 CNY per-token 价。

help.aliyun.com/zh/model-studio/model-pricing 为 SSR 大页,用 Playwright 取渲染后文本
最稳。文本模型按阶梯计价,渲染后行序列形如:
    qwen3-max
    中国内地
    非思考和思考模式
    0<Token≤32K
    2.5元
    10元
取每个模型主别名(跳过 -YYYY-MM-DD 日期快照)的第一档 输入/输出(元/百万tokens)。
parse(text) 只吃渲染后文本,可离线 fixture 单测。
"""
from __future__ import annotations

import re

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

_NAME = re.compile(r"^(qwen[\w.\-]+)$", re.I)
# 价格行:纯"12元",或带活动标记"原价12元 限时5折"——取行内第一个"(原价)?数字元"的数字。
# 用刊例原价(与智谱口径一致):限时折扣是营销活动会变,原价才是稳定官方定价。
_PRICE = re.compile(r"^(?:原价)?\s*([\d.]+)\s*元")
_DATE_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}")
# 非文本模型关键词(多模态/向量等)→ 跳过
_SKIP = ("-vl", "vl-", "omni", "image", "audio", "tts", "asr", "ocr",
         "embedding", "rerank", "-mt", "wan", "cosyvoice", "paraformer")


class AliyunScraper(BaseScraper):
    provider = "aliyun"
    channel = "official"
    source_url = "https://help.aliyun.com/zh/model-studio/model-pricing"
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
                await page.wait_for_timeout(6000)
                return await page.inner_text("body")
            finally:
                await browser.close()

    def parse(self, text: str) -> list[RawPrice]:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        results: dict[str, RawPrice] = {}
        for i, line in enumerate(lines):
            m = _NAME.match(line)
            if not m:
                continue
            name = m.group(1)
            low = name.lower()
            if any(k in low for k in _SKIP) or _DATE_SNAPSHOT.search(name):
                continue
            if name in results:
                continue
            # 向后取前两个「X元」价格行(遇到下一个模型名停止)
            prices: list[float] = []
            for nxt in lines[i + 1 : i + 20]:
                if _NAME.match(nxt):
                    break
                pm = _PRICE.match(nxt)
                if pm:
                    prices.append(float(pm.group(1)))
                    if len(prices) >= 2:
                        break
            if len(prices) >= 2 and not (prices[0] == 0 and prices[1] == 0):
                results[name] = RawPrice(
                    provider=self.provider,
                    channel=self.channel,
                    model=name,
                    region=Region.CN,
                    currency=Currency.CNY,
                    input_per_1m=prices[0],
                    output_per_1m=prices[1],
                    source_url=self.source_url,
                )
        return list(results.values())
