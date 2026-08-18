"""通义千问(阿里云百炼)官网定价抓取器 —— 官方 CNY per-token 价。

help.aliyun.com/zh/model-studio/model-pricing 为 SSR 大页,用 Playwright 取渲染后文本
最稳。文本模型按上下文阶梯计价,渲染后行序列形如:
    qwen3-max
    中国内地
    非思考和思考模式
    0<Token≤32K
    2.5元
    10元
    32K<Token≤128K
    3元
    12元
每个阶梯产出一条带 context_range 的 RawPrice(跳过 -YYYY-MM-DD 日期快照)。
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
# 阶梯行:"0<Token≤32K" / "32K<Token≤128K" / "Token>128K"
_TIER = re.compile(r"^(?:(\d+(?:\.\d+)?\s*[KkMm]?)\s*<\s*)?Token\s*(≤|>=?|＞)\s*(\d+(?:\.\d+)?\s*[KkMm]?)?", re.I)
# 非文本模型关键词(多模态/向量等)→ 跳过
_SKIP = ("-vl", "vl-", "omni", "image", "audio", "tts", "asr", "ocr",
         "embedding", "rerank", "-mt", "wan", "cosyvoice", "paraformer")


def _norm_bound(value: str | None) -> str:
    """'32K' → '32k','1M' → '1024k'(统一以 k 表示,便于排序比较)。"""
    if not value:
        return ""
    m = re.search(r"(\d+(?:\.\d+)?)\s*([KkMm])", value)
    if not m:
        return value.strip().lower()
    n = float(m.group(1))
    if m.group(2).lower() == "m":
        n *= 1000  # 1M → 1000k,统一以 k 表示
    return f"{int(n)}k" if n == int(n) else f"{n}k"


def _norm_tier(m: re.Match) -> str | None:
    """阶梯行归一:'0<Token≤32K' → '0-32k';'Token>128K' → '>128k'。"""
    lo, op, hi = m.group(1), m.group(2), m.group(3)
    lo_n, hi_n = _norm_bound(lo), _norm_bound(hi)
    if lo_n and hi_n:
        return f"{lo_n}-{hi_n}"
    if hi_n:
        return f">{hi_n}" if op.startswith((">", "＞")) else f"0-{hi_n}"
    return None


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
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
            try:
                page = await browser.new_page(user_agent=settings.user_agent)
                await self._prepare_page(page)
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(6000)
                return await page.inner_text("body")
            finally:
                await browser.close()

    def parse(self, text: str) -> list[RawPrice]:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        results: dict[tuple[str, str | None], RawPrice] = {}
        for i, line in enumerate(lines):
            m = _NAME.match(line)
            if not m:
                continue
            name = m.group(1)
            low = name.lower()
            if any(k in low for k in _SKIP) or _DATE_SNAPSHOT.search(name):
                continue
            # 向后扫描该模型的阶梯与价格(遇到下一个模型名停止)
            tiers: list[tuple[str | None, float, float]] = []
            current_tier: str | None = None
            prices: list[float] = []
            for nxt in lines[i + 1 : i + 40]:
                if _NAME.match(nxt):
                    break
                tm = _TIER.match(nxt)
                if tm and "token" in nxt.lower():
                    if len(prices) >= 2:
                        tiers.append((current_tier, prices[0], prices[1]))
                    current_tier = _norm_tier(tm)
                    prices = []
                    continue
                pm = _PRICE.match(nxt)
                if pm:
                    prices.append(float(pm.group(1)))
            if len(prices) >= 2:
                tiers.append((current_tier, prices[0], prices[1]))
            for tier, inp, out in tiers:
                if inp == 0 and out == 0:
                    continue
                key = (name, tier)
                if key in results:
                    continue
                results[key] = RawPrice(
                    provider=self.provider,
                    channel=self.channel,
                    model=name,
                    region=Region.CN,
                    currency=Currency.CNY,
                    input_per_1m=inp,
                    output_per_1m=out,
                    context_range=tier,
                    source_url=self.source_url,
                )
        return list(results.values())
