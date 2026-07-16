"""各官方厂商的视觉抓取器 —— 官方定价的主源(vision-* source)。

统一走 VisionScraper:截图 → Claude 结构化提取 → 单位归一。指向各厂商官方定价页,
与对应的正则抓取器同 provider/channel(official),但 source 前缀 vision-,故两者在
置信度聚合里互为印证而非互相覆盖(唯一键已含 source)。

页面渲染前提各异:
- MiniMax / 智谱 / 百度 / 腾讯:定价表能正常渲染,视觉提取可用。
- 通义(阿里)/ DeepSeek:官方页是干净 SSR,正则更稳(通义 49 个模型跨多屏,视觉易漏),
  故保留正则为官方源,不加视觉版。
- Kimi:价格表组件无头不渲染,见 kimi.py。
"""
from __future__ import annotations

import re

from app.models.pricing import Currency, Region
from app.scrapers.vision_base import VisionScraper


def _slug(shown: str) -> str:
    s = shown.strip().lower()
    s = re.sub(r"\s+", "-", s)
    return re.sub(r"-{2,}", "-", s).strip("-")


class MiniMaxVisionScraper(VisionScraper):
    provider = "minimax"
    channel = "official"
    source_url = "https://platform.minimaxi.com/docs/guides/pricing-paygo"
    region = Region.CN
    currency = Currency.CNY

    def map_model(self, shown: str) -> str:
        return _slug(shown)


class ZhipuVisionScraper(VisionScraper):
    provider = "zhipu"
    channel = "official"
    source_url = "https://open.bigmodel.cn/pricing"
    region = Region.CN
    currency = Currency.CNY

    def map_model(self, shown: str) -> str:
        return _slug(shown)


class BaiduVisionScraper(VisionScraper):
    provider = "baidu"
    channel = "official"
    source_url = "https://cloud.baidu.com/doc/qianfan/s/wmh4sv6ya"
    region = Region.CN
    currency = Currency.CNY

    def map_model(self, shown: str) -> str:
        return _slug(shown)


class TencentVisionScraper(VisionScraper):
    provider = "tencent"
    channel = "official"
    source_url = "https://cloud.tencent.com/document/product/1729/97731"
    region = Region.CN
    currency = Currency.CNY

    def map_model(self, shown: str) -> str:
        return _slug(shown)
