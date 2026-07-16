"""OpenRouter 定价抓取器 —— 大幅补充模型覆盖(含大量国内厂商),USD。

来源:https://openrouter.ai/api/v1/models,纯 JSON、每日更新,涵盖 OpenAI/Anthropic/
Google 及 DeepSeek/Qwen(通义)/GLM(智谱)/Kimi(月之暗面)/MiniMax/腾讯混元/百度文心/
阶跃星辰等国内厂商。pricing 为「每 token 美元」字符串,乘 1e6 归一到每 1M tokens。

id 形如 `vendor/model`,vendor 前缀映射到本项目 provider。渠道统一记为 openrouter、
地区 intl、货币 USD(这是国内模型在 OpenRouter 上的美元价;真实 CNY 由官网抓取器/
override 层提供)。
"""
from __future__ import annotations

import json

from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

# OpenRouter vendor 前缀 → 本项目 provider(公司口径)
_VENDOR_MAP = {
    # 国内
    "deepseek": "deepseek", "qwen": "aliyun", "z-ai": "zhipu", "thudm": "zhipu",
    "moonshotai": "moonshot", "minimax": "minimax", "stepfun": "stepfun",
    "tencent": "tencent", "baidu": "baidu", "01-ai": "yi", "baichuan": "baichuan",
    # 国外
    "openai": "openai", "anthropic": "anthropic", "google": "google",
    "x-ai": "xai", "meta-llama": "meta", "mistralai": "mistral", "cohere": "cohere",
}

# 已被 DeepSeek/LiteLLM 抓取器覆盖的厂商:此处跳过,避免重复渠道噪音
_SKIP_PROVIDERS = {"openai", "anthropic", "google"}


class OpenRouterScraper(BaseScraper):
    provider = "openrouter"
    channel = "openrouter"
    source_url = "https://openrouter.ai/api/v1/models"

    def parse(self, text: str) -> list[RawPrice]:
        data = json.loads(text).get("data", [])
        results: list[RawPrice] = []
        for m in data:
            mid = m.get("id", "")
            if "/" not in mid or ":free" in mid:  # 跳过异常/免费变体
                continue
            vendor_prefix = mid.split("/", 1)[0].lower()
            provider = _VENDOR_MAP.get(vendor_prefix)
            if provider is None or provider in _SKIP_PROVIDERS:
                continue

            pricing = m.get("pricing") or {}
            inp = self._per_m(pricing.get("prompt"))
            out = self._per_m(pricing.get("completion"))
            if not inp and not out:  # 免费或缺价
                continue

            results.append(
                RawPrice(
                    provider=provider,
                    channel=self.channel,
                    model=mid.split("/", 1)[1],
                    region=Region.INTL,
                    currency=Currency.USD,
                    input_per_1m=inp,
                    output_per_1m=out,
                    cached_input_per_1m=self._per_m(pricing.get("input_cache_read")),
                    cache_write_per_1m=self._per_m(pricing.get("input_cache_write")),
                    context_window=m.get("context_length"),
                    source_url="https://openrouter.ai/models",
                )
            )
        return results

    @staticmethod
    def _per_m(cost: str | float | None) -> float | None:
        if cost is None:
            return None
        try:
            v = float(cost) * 1_000_000
        except (TypeError, ValueError):
            return None
        return round(v, 6) if v > 0 else None
