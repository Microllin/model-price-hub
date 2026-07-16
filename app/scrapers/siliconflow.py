"""SiliconFlow(硅基流动)抓取器 —— 大量国内模型的真实 CNY per-token 价。

云托管平台,聚合托管 Qwen(通义)/GLM(智谱)/DeepSeek/Kimi/MiniMax/百度等国内厂商
模型,并以「￥X / M Tokens」按 token 人民币计价。定价页为 Next.js 客户端渲染,需
Playwright:渲染后取 body 可见文本,再按「模型名 … 输入 ￥.. 输出 ￥..」正则配对。

parse(text) 只吃「渲染后的纯文本」,与网络分离,可用离线 fixture 单测。
未启用 Playwright(MPH_USE_PLAYWRIGHT)时优雅跳过(返回空)。
"""
from __future__ import annotations

import re

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

# SiliconFlow 模型 id 的 vendor 前缀 → 本项目 provider
_VENDOR_MAP = {
    "deepseek-ai": "deepseek", "qwen": "aliyun", "tongyi-mai": "aliyun",
    "moonshotai": "moonshot", "minimaxai": "minimax", "minimax": "minimax",
    "zai": "zhipu", "z-ai": "zhipu", "zai-org": "zhipu", "thudm": "zhipu", "zhipuai": "zhipu",
    "baidu": "baidu", "01-ai": "yi", "internlm": "internlm", "tencent": "tencent",
    "stepfun-ai": "stepfun", "baichuan-inc": "baichuan", "meituan-longcat": "meituan",
}

# 非文本模型关键词(图像/音视频/向量等,per-token 语义不同)→ 跳过
_SKIP_KEYWORDS = (
    "image", "z-image", "embedding", "rerank", "reranker", "bge", "tts", "asr",
    "audio", "video", "cosyvoice", "whisper", "wan", "flux", "kolors", "sd3",
    "stable-diffusion", "speech", "voice",
)

# 「vendor/Model … 输入 ￥X / M … 输出 ￥Y / M」——允许中间夹发布时间/标签,限长防跨模型误配
_PAT = re.compile(
    r"([A-Za-z0-9][\w.\-]*(?:/[\w.\-]+)+)"          # 模型 id(可多段,如 Pro/moonshotai/Kimi)
    r"[\s\S]{0,160}?输入[:：]\s*￥?\s*([\d.]+)\s*/\s*M"   # 输入价
    r"[\s\S]{0,100}?输出[:：]\s*￥?\s*([\d.]+)\s*/\s*M",  # 输出价
)


class SiliconFlowScraper(BaseScraper):
    provider = "siliconflow"
    channel = "siliconflow"
    source_url = "https://siliconflow.cn/models"
    requires_render = True

    async def fetch(self) -> list[RawPrice]:
        if not settings.use_playwright:
            return []  # 需 MPH_USE_PLAYWRIGHT=1 + 已装 chromium
        text = await self._render_text(self.source_url)
        return self.parse(text)

    async def _render_text(self, url: str) -> str:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = await browser.new_page(user_agent=settings.user_agent)
                await page.goto(url, wait_until="networkidle", timeout=45000)
                await page.wait_for_timeout(3000)  # 等首屏卡片渲染
                # 页面为懒加载,滚动到底触发加载更多模型
                prev = 0
                for _ in range(30):
                    await page.mouse.wheel(0, 20000)
                    await page.wait_for_timeout(1200)
                    height = await page.evaluate("document.body.scrollHeight")
                    if height == prev:
                        break
                    prev = height
                return await page.inner_text("body")
            finally:
                await browser.close()

    def parse(self, text: str) -> list[RawPrice]:
        results: dict[str, RawPrice] = {}
        for mid, inp, out in _PAT.findall(text):
            low = mid.lower()
            if any(k in low for k in _SKIP_KEYWORDS):
                continue
            input_p, output_p = float(inp), float(out)
            if input_p == 0 and output_p == 0:  # 免费/占位
                continue
            results[mid] = RawPrice(
                provider=self._vendor(mid),
                channel=self.channel,
                model=mid,
                region=Region.CN,
                currency=Currency.CNY,
                input_per_1m=input_p,
                output_per_1m=output_p,
                source_url=self.source_url,
            )
        return list(results.values())

    @staticmethod
    def _vendor(mid: str) -> str:
        # 取倒数第二段作为 vendor 前缀(兼容 Pro/vendor/Model);再查映射
        parts = mid.split("/")
        prefix = parts[-2].lower() if len(parts) >= 2 else parts[0].lower()
        return _VENDOR_MAP.get(prefix, prefix)
