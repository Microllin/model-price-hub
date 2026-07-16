"""抓取器注册表:集中列出启用的抓取器实例。

架构:官方定价以【视觉】为主源(vision-* source),【正则】抓取器降为验证器 ——
两者同 provider/channel(official)但不同 source,在置信度聚合里互为印证。
唯一键已含 source,故同一模型的视觉值与正则值共存,不互相覆盖。
无视觉凭据/页面不渲染时,正则仍兜住官方价(优雅降级)。
"""
from __future__ import annotations

from app.scrapers.aliyun import AliyunScraper
from app.scrapers.baidu import BaiduScraper
from app.scrapers.base import BaseScraper
from app.scrapers.deepseek import DeepSeekScraper
from app.scrapers.kimi import KimiScraper
from app.scrapers.litellm_json import LiteLLMScraper
from app.scrapers.minimax import MiniMaxScraper
from app.scrapers.openrouter import OpenRouterScraper
from app.scrapers.ppio import PPIOScraper
from app.scrapers.siliconflow import SiliconFlowScraper
from app.scrapers.tencent import TencentScraper
from app.scrapers.vision_official import (
    BaiduVisionScraper,
    MiniMaxVisionScraper,
    TencentVisionScraper,
    ZhipuVisionScraper,
)
from app.scrapers.zhipu import ZhipuScraper


def all_scrapers() -> list[BaseScraper]:
    """返回本轮要运行的全部抓取器实例。新增厂商在此登记。"""
    return [
        # ---- 官方定价 · 视觉主源(需 Playwright + 视觉凭据)----
        MiniMaxVisionScraper(),   # MiniMax 官网 · 视觉
        ZhipuVisionScraper(),     # 智谱 GLM 官网 · 视觉
        BaiduVisionScraper(),     # 百度文心官网 · 视觉
        TencentVisionScraper(),   # 腾讯混元官网 · 视觉
        KimiScraper(),            # Kimi 官网 · 视觉(表格无头不渲染,见 kimi.py)
        # ---- 官方定价 · 正则(验证器 / 无视觉时兜底)----
        DeepSeekScraper(),        # 国内官方 · CNY + USD(静态表,正则更稳,官方源)
        ZhipuScraper(),           # 智谱 GLM · 正则(验证 vision-zhipu)
        MiniMaxScraper(),         # MiniMax · 正则(验证 vision-minimax)
        AliyunScraper(),          # 通义千问 · 正则(SSR 大页,49 模型,保留为官方源)
        BaiduScraper(),           # 百度文心 · 正则(验证 vision-baidu)
        TencentScraper(),         # 腾讯混元 · 正则(验证 vision-tencent)
        # ---- 非官方渠道 · USD/CNY(旁证 + 覆盖面)----
        LiteLLMScraper(),         # 国外主流 + 云托管 · USD
        OpenRouterScraper(),      # 国内外聚合 · USD
        SiliconFlowScraper(),     # 国内三方托管 · CNY
        PPIOScraper(),            # 国内三方托管 · CNY
    ]
