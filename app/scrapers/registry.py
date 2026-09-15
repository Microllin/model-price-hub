"""抓取器注册表:集中列出启用的抓取器实例。

架构:官方定价以【正则】为主源(零 token 消耗),【视觉】为定期验证器 ——
两者同 provider/channel(official)但不同 source,在置信度聚合里互为印证。
唯一键已含 source,故同一模型的视觉值与正则值共存,不互相覆盖。
视觉源受 vision_run_every_n 控制,默认每 5 轮跑一次,显著减少 token 消耗。
"""
from __future__ import annotations

from app.scrapers.aliyun import AliyunScraper
from app.scrapers.amazon import AmazonBedrockScraper
from app.scrapers.anthropic import AnthropicScraper
from app.scrapers.baidu import BaiduScraper
from app.scrapers.base import BaseScraper
from app.scrapers.cohere import CohereScraper
from app.scrapers.deepseek import DeepSeekScraper
from app.scrapers.google import GoogleScraper
from app.scrapers.iflytek import IflytekScraper
from app.scrapers.kimi import KimiScraper
from app.scrapers.litellm_json import LiteLLMScraper
from app.scrapers.minimax import MiniMaxScraper
from app.scrapers.mistral import MistralScraper
from app.scrapers.openai import OpenAIScraper
from app.scrapers.openrouter import OpenRouterScraper
from app.scrapers.perplexity import PerplexityScraper
from app.scrapers.volcengine import VolcEngineScraper
from app.scrapers.ppio import PPIOScraper
from app.scrapers.siliconflow import SiliconFlowScraper
from app.scrapers.stepfun import StepFunScraper
from app.scrapers.tencent import TencentScraper
from app.scrapers.vision_official import (
    BaiduVisionScraper,
    MiniMaxVisionScraper,
    TencentVisionScraper,
    ZhipuVisionScraper,
)
from app.scrapers.xai import XAIScraper
from app.scrapers.yi import YiScraper
from app.scrapers.zhipu import ZhipuScraper


def all_scrapers() -> list[BaseScraper]:
    """返回本轮要运行的全部抓取器实例。新增厂商在此登记。"""
    return [
        # ---- 官方定价 · 视觉验证器(受 vision_run_every_n 控制,默认每 5 轮跑一次)----
        MiniMaxVisionScraper(),   # MiniMax 官网 · 视觉
        ZhipuVisionScraper(),     # 智谱 GLM 官网 · 视觉
        BaiduVisionScraper(),     # 百度文心官网 · 视觉
        TencentVisionScraper(),   # 腾讯混元官网 · 视觉
        KimiScraper(),            # Kimi 官网 · 视觉(有头模式,无头不渲染价格表)
        # ---- 官方定价 · 正则/HTML 主源(零 token 消耗)—— 国外 ----
        DeepSeekScraper(),        # DeepSeek 官方 · CNY + USD(静态表,正则更稳)
        AnthropicScraper(),       # Anthropic 官方 · USD(SSR 表,含缓存写双档/fast/batch)
        OpenAIScraper(),          # OpenAI 官方 · USD(standard/batch/flex/fast)
        GoogleScraper(),          # Google Gemini 官方 · USD(分档/促销价/缓存)
        XAIScraper(),             # xAI Grok 官方 · USD(新增)
        MistralScraper(),         # Mistral AI 官方 · USD(新增)
        CohereScraper(),          # Cohere 官方 · USD(新增)
        PerplexityScraper(),      # Perplexity 官方 · USD(新增)
        AmazonBedrockScraper(),   # Amazon Nova 官方 · USD(新增,只抽 Amazon 自有模型)
        # ---- 官方定价 · 正则/Playwright 主源(零 token)—— 国内 ----
        VolcEngineScraper(),      # 火山引擎/字节 · CNY(豆包/Seed,分档+常规/低延迟/批量)
        ZhipuScraper(),           # 智谱 GLM · 正则(验证 vision-zhipu)
        MiniMaxScraper(),         # MiniMax · 正则(验证 vision-minimax)
        AliyunScraper(),          # 通义千问 · 正则(SSR 大页,49 模型,保留为官方源)
        BaiduScraper(),           # 百度文心 · 正则(验证 vision-baidu)
        TencentScraper(),         # 腾讯混元 · 正则(验证 vision-tencent)
        StepFunScraper(),         # 阶跃星辰 · 正则(新增,SPA 需 Playwright)
        IflytekScraper(),         # 科大讯飞星火 · 正则(SPA 需 Playwright)
        YiScraper(),              # 零一万物 Yi · 正则(SPA 需 Playwright)
        # ---- 非官方渠道 · USD/CNY(旁证 + 覆盖面)—— 零 token ----
        LiteLLMScraper(),         # 国外主流 + 云托管 · USD
        OpenRouterScraper(),      # 国内外聚合 · USD
        SiliconFlowScraper(),     # 国内三方托管 · CNY
        PPIOScraper(),            # 国内三方托管 · CNY
    ]
