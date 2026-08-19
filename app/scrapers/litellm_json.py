"""LiteLLM 公开定价 JSON 抓取器 —— 全量模型 USD 价格旁证。

来源:BerriAI/litellm 仓库维护的 model_prices_and_context_window.json，机器可读、
社区每日更新，包含 3000+ 模型。单价字段为「每 token 美元」，乘 1e6 归一到每 1M tokens。

设计:
- **不做家族白名单过滤**，所有 mode=chat/responses 且有价格的模型全部纳入
  (前端以筛选代替截断，数据层不丢信息)。
- litellm_provider 映射为本项目 provider/channel，未识别的 provider 回退为
  provider=litellm_provider, channel=third_party(仍纳入作为旁证)。
"""
from __future__ import annotations

import json

from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

# litellm_provider → (本项目 provider, channel)
# 先查精确映射，未命中则自动回退为 (None, "third_party")
_CHANNEL_MAP = {
    # ———— 官方直连 ————
    "openai": ("openai", "official"),
    "anthropic": ("anthropic", "official"),
    "gemini": ("google", "official"),
    "xai": ("xai", "official"),
    "mistral": ("mistral", "official"),
    "deepseek": ("deepseek", "official"),
    "moonshot": ("moonshot", "official"),
    "minimax": ("minimax", "official"),
    "cohere_chat": ("cohere", "official"),
    "ai21": ("ai21", "official"),
    "zai": ("zhipu", "official"),
    "perplexity": ("perplexity", "official"),
    "meta_llama": ("meta", "official"),
    "meta": ("meta", "official"),
    # ———— 国内厂商 ————
    "volcengine": ("bytedance", "volcengine"),
    "dashscope": ("aliyun", "aliyun-bailian"),
    "tencent": ("tencent", "official"),
    "gigachat": ("sberbank", "official"),
    # ———— 云托管 / 聚合 ————
    "vertex_ai-language-models": ("google", "vertex"),
    "vertex_ai-anthropic_models": ("anthropic", "vertex"),
    "vertex_ai-mistral_models": ("mistral", "vertex"),
    "vertex_ai-llama_models": ("meta", "vertex"),
    "vertex_ai-ai21_models": ("ai21", "vertex"),
    "vertex_ai-deepseek_models": ("deepseek", "vertex"),
    "vertex_ai-qwen_models": ("aliyun", "vertex"),
    "vertex_ai-openai_models": ("openai", "vertex"),
    "vertex_ai-zai_models": ("zhipu", "vertex"),
    "vertex_ai-minimax_models": ("minimax", "vertex"),
    "vertex_ai-moonshot_models": ("moonshot", "vertex"),
    "vertex_ai": ("google", "vertex"),
    "bedrock": (None, "bedrock"),
    "bedrock_converse": (None, "bedrock"),
    "bedrock_mantle": (None, "bedrock"),
    "azure": ("openai", "azure"),
    "azure_ai": ("openai", "azure"),
    # ———— 推理平台 ————
    "fireworks_ai": (None, "fireworks"),
    "together_ai": (None, "together"),
    "groq": (None, "groq"),
    "cerebras": (None, "cerebras"),
    "sambanova": (None, "sambanova"),
    "deepinfra": (None, "deepinfra"),
    "novita": (None, "novita"),
    "lambda_ai": (None, "lambda"),
    "hyperbolic": (None, "hyperbolic"),
    "nebius": (None, "nebius"),
    "nscale": (None, "nscale"),
    "scaleway": (None, "scaleway"),
    "ovhcloud": (None, "ovhcloud"),
    "crusoe": (None, "crusoe"),
    "replicate": (None, "replicate"),
    "baseten": (None, "baseten"),
    "cloudflare": (None, "cloudflare"),
    "databricks": (None, "databricks"),
    "snowflake": (None, "snowflake"),
    "oci": (None, "oci"),
    "watsonx": (None, "watsonx"),
    "gmi": (None, "gmi"),
    "anyscale": (None, "anyscale"),
    "openrouter": (None, "openrouter"),
    "amazon_nova": ("amazon", "bedrock"),
}

# bedrock 模型 id 前缀 → 底层厂商
_BEDROCK_VENDOR = {
    "anthropic": "anthropic", "meta": "meta", "amazon": "amazon",
    "cohere": "cohere", "mistral": "mistral", "ai21": "ai21", "deepseek": "deepseek",
}

# 模型名关键词兼作 bedrock 厂商推断和推理平台 provider 回退
_VENDOR_KEYWORDS = (
    ("claude", "anthropic"), ("llama", "meta"), ("titan", "amazon"),
    ("nova", "amazon"), ("command", "cohere"), ("mixtral", "mistral"),
    ("mistral", "mistral"), ("jamba", "ai21"), ("deepseek", "deepseek"),
    ("qwen", "aliyun"), ("gemma", "google"), ("phi", "microsoft"),
    ("starcoder", "bigcode"), ("dbrx", "databricks"), ("yi-", "01ai"),
    ("doubao", "bytedance"), ("seed", "bytedance"),
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
            lp = spec.get("litellm_provider", "")
            in_cost = spec.get("input_cost_per_token")
            out_cost = spec.get("output_cost_per_token")
            if in_cost is None and out_cost is None:
                continue
            # 零价模型可能是免费层或未填，跳过以免拉低交叉验证准确度
            if (in_cost or 0) <= 0 and (out_cost or 0) <= 0:
                continue

            model = name.split("/")[-1]

            if lp in _CHANNEL_MAP:
                provider, channel = _CHANNEL_MAP[lp]
            else:
                # 未识别的 provider 回退为第三方旁证，仍纳入
                provider, channel = None, "third_party"

            if provider is None:
                provider = self._infer_vendor(model, lp)

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
    def _infer_vendor(model: str, litellm_provider: str) -> str:
        """bedrock/推理平台的模型名推断底层厂商。"""
        # 1) bedrock 风格:vendor.model → 扒厂商前缀
        for seg in model.lower().split("."):
            if seg in _BEDROCK_VENDOR:
                return _BEDROCK_VENDOR[seg]
        # 2) 模型名关键词
        low = model.lower()
        for kw, vendor in _VENDOR_KEYWORDS:
            if kw in low:
                return vendor
        # 3) 用 litellm_provider 本身作为 provider(如 fireworks_ai → fireworks_ai)
        return litellm_provider or "unknown"

    @staticmethod
    def _per_m(cost_per_token: float | None) -> float | None:
        if cost_per_token is None:
            return None
        return round(cost_per_token * 1_000_000, 6)

    @staticmethod
    def _dedup(rows: list[RawPrice]) -> list[RawPrice]:
        seen: dict[tuple, RawPrice] = {}
        for r in rows:
            seen[r.key()] = r  # 同键保留后者
        return list(seen.values())
