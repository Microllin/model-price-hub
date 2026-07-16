"""月之暗面 Kimi 官网定价抓取器 —— 视觉提取(官方 CNY)。

Kimi 每个模型有独立定价子页(platform.kimi.com/docs/pricing/chat-*),单位为每 1M tokens。
走视觉路径:逐个 URL 滚动截图 → Claude 结构化提取。需 ANTHROPIC_AUTH_TOKEN/MPH_ANTHROPIC_API_KEY。

⚠️ 已知问题:Kimi 定价子页的【价格表组件】在无头 Chromium 里不渲染(无 XHR、无像素),
故本抓取器在无头环境下可能返回空——视觉也读不了没渲染出来的东西。可选方案:改用有头
浏览器(headless=False)或等 Kimi 修复其表格的 SSR。其它厂商(MiniMax/智谱/通义等)的
定价表能正常渲染,视觉提取工作良好(见 VisionScraper)。
"""
from __future__ import annotations

import re

from app.models.pricing import Currency, Region
from app.scrapers.vision_base import VisionScraper

_BASE = "https://platform.kimi.com/docs/pricing"


class KimiScraper(VisionScraper):
    provider = "moonshot"
    channel = "official"
    source_url = f"{_BASE}/chat"
    region = Region.CN
    currency = Currency.CNY
    # 每个模型的独立定价子页(单位:每 1M tokens)
    screenshot_urls = [
        f"{_BASE}/chat-k27-code",
        f"{_BASE}/chat-k26",
        f"{_BASE}/chat-k25",
        f"{_BASE}/chat-v1",
    ]

    def map_model(self, shown: str) -> str:
        s = shown.strip().lower()
        s = re.sub(r"\s+", "-", s)
        return s
