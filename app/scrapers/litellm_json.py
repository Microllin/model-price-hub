"""LiteLLM 公开定价 JSON 抓取器 —— 国外主流 + 云托管平台的 USD 价格。

来源:BerriAI/litellm 仓库维护的 model_prices_and_context_window.json,机器可读、
社区每日更新。单价字段为「每 token 美元」,乘 1e6 归一到每 1M tokens。

该 JSON 含近 3000 个模型,这里只保留「前沿主力」家族(通过 FRONTIER_FAMILIES 子串
过滤),并把 litellm_provider 映射为本项目的 provider/channel。名称按子串匹配,
不硬编码具体版本号,能自动纳入同族新版本。
"""
from __future__ import annotations

import json

from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

# 前沿主力家族(子串,小写匹配)。新增家族在此扩充即可。
FRONTIER_FAMILIES = (
    "gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "o4", "chatgpt",
    "claude-3", "claude-4", "claude-opus", "claude-sonnet", "claude-haiku",
    "gemini-1.5", "gemini-2", "gemini-3",
    "grok", "mistral-large",
)

# litellm_provider → (本项目 provider, channel);bedrock/azure 的 provider 从模型名前缀再细分
_CHANNEL_MAP = {
    "openai": ("openai", "official"),
    "anthropic": ("anthropic", "official"),
    "gemini": ("google", "official"),
    "vertex_ai-language-models": ("google", "vertex"),
    "xai": ("xai", "official"),
    "mistral": ("mistral", "official"),
    "bedrock": (None, "bedrock"),
    "bedrock_converse": (None, "bedrock"),
    "azure": ("openai", "azure"),
    "azure_ai": ("openai", "azure"),
}

# bedrock 模型 id 前缀 → 底层厂商
_BEDROCK_VENDOR = {
    "anthropic": "anthropic", "meta": "meta", "amazon": "amazon",
    "cohere": "cohere", "mistral": "mistral", "ai21": "ai21", "deepseek": "deepseek",
}

# 模型名关键词兜底(裸 id 无厂商前缀时用)
_VENDOR_KEYWORDS = (
    ("claude", "anthropic"), ("llama", "meta"), ("titan", "amazon"),
    ("nova", "amazon"), ("command", "cohere"), ("mixtral", "mistral"),
    ("mistral", "mistral"), ("jamba", "ai21"), ("deepseek", "deepseek"),
)


class LiteLLMScraper(BaseScraper):
    provider = "litellm"  # 聚合源标识;实际每条会被改写为真实 provider
    source_url = (
        "https://raw.githubusercontent.com/BerriAI/litellm/main/"
        "model_prices_and_context_window.json"
    )

    def parse(self, text: str) -> list[RawPrice]:
        data = json.loads(text)
        results: list[RawPrice] = []
        for name, spec in data.items():
            if not isinstance(spec, dict):
                continue
            if spec.get("mode") not in (None, "chat", "responses"):
                continue
            lp = spec.get("litellm_provider")
            if lp not in _CHANNEL_MAP:
                continue
            model = name.split("/")[-1]
            if not self._is_frontier(model):
                continue
            in_cost = spec.get("input_cost_per_token")
            out_cost = spec.get("output_cost_per_token")
            if in_cost is None and out_cost is None:
                continue

            provider, channel = _CHANNEL_MAP[lp]
            if provider is None:  # bedrock:从模型名前缀推断底层厂商
                provider = self._bedrock_vendor(model)

            results.append(
                RawPrice(
                    provider=provider,
                    channel=channel,
                    model=model,
                    region=Region.INTL,
                    currency=Currency.USD,
                    input_per_1m=self._per_m(in_cost),
                    output_per_1m=self._per_m(out_cost),
                    cached_input_per_1m=self._per_m(spec.get("cache_read_input_token_cost")),
                    cache_write_per_1m=self._per_m(spec.get("cache_creation_input_token_cost")),
                    context_window=spec.get("max_input_tokens"),
                    max_output=spec.get("max_output_tokens"),
                    source_url=self.source_url,
                )
            )
        return self._dedup(results)

    # ---- helpers ----
    @staticmethod
    def _is_frontier(model: str) -> bool:
        m = model.lower()
        return any(fam in m for fam in FRONTIER_FAMILIES)

    @staticmethod
    def _per_m(cost_per_token: float | None) -> float | None:
        if cost_per_token is None:
            return None
        return round(cost_per_token * 1_000_000, 6)

    @staticmethod
    def _bedrock_vendor(model: str) -> str:
        # 1) 扫描各段,取第一个已知厂商(自动跳过区域前缀 us./eu./au./jp. 等)
        for seg in model.lower().split("."):
            if seg in _BEDROCK_VENDOR:
                return _BEDROCK_VENDOR[seg]
        # 2) 无厂商前缀的裸 id → 按模型名关键词兜底
        low = model.lower()
        for kw, vendor in _VENDOR_KEYWORDS:
            if kw in low:
                return vendor
        return "aws"

    @staticmethod
    def _dedup(rows: list[RawPrice]) -> list[RawPrice]:
        seen: dict[tuple, RawPrice] = {}
        for r in rows:
            seen[r.key()] = r  # 同键保留后者
        return list(seen.values())
