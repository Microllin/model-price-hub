"""模型 ID 归一化 + 官方渠道判定。

不同来源对同一模型写法各异,例如:
  deepseek 官网      deepseek-v4-pro
  litellm            deepseek/deepseek-chat、anthropic.claude-sonnet-4-20250514-v1:0
  openrouter         deepseek-v4-pro(已去 vendor 前缀)
  siliconflow        deepseek-ai/DeepSeek-V4-Pro、Pro/moonshotai/Kimi-K2.6
canonicalize() 尽力把它们归一到同一个 canonical id,才能跨来源交叉验证同一模型的价格。

归一是 best-effort:归错只会让某模型的印证源变少(置信度偏低),不会串价。
"""
from __future__ import annotations

import re

# 出现在 `vendor.model`(bedrock 风格)里的厂商前缀,归一时剥离
_VENDOR_DOT_PREFIXES = {
    "anthropic", "meta", "amazon", "cohere", "mistral", "ai21", "deepseek", "qwen",
}
# bedrock 跨区域前缀
_REGION_PREFIXES = {"us", "eu", "apac", "global", "au", "jp", "ca", "sa"}

# 官方直营渠道(模型原厂自己定价)→ 参与置信度;其余(openrouter/siliconflow/bedrock/
# azure/vertex 等三方托管/聚合)视为非官方
OFFICIAL_CHANNELS = {"official", "aliyun-bailian", "volcengine"}


def is_official(channel: str) -> bool:
    return channel in OFFICIAL_CHANNELS


def canonicalize(model: str) -> str:
    """把模型名归一到用于跨源匹配的 canonical id。"""
    s = model.strip().lower()

    # 1) 取最后一段路径:deepseek-ai/deepseek-v4-pro、pro/moonshotai/kimi-k2.6 → 末段
    if "/" in s:
        s = s.split("/")[-1]

    # 2) bedrock 风格 vendor.model / region.vendor.model → 剥前缀
    parts = s.split(".")
    while len(parts) > 1 and (parts[0] in _REGION_PREFIXES or parts[0] in _VENDOR_DOT_PREFIXES):
        parts = parts[1:]
    s = ".".join(parts)

    # 3) 去掉常见后缀噪声
    s = re.sub(r"[:@]free$", "", s)
    s = re.sub(r"-v\d+:\d+$", "", s)        # -v1:0
    s = re.sub(r"[@:]\d{6,8}$", "", s)       # @20250514
    s = re.sub(r"-\d{6,8}$", "", s)          # -20250514
    s = re.sub(r"-latest$", "", s)
    s = re.sub(r"-instruct$", "", s)

    # 4) 统一分隔符与多余符号
    s = s.replace("_", "-").replace(" ", "-")
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s
