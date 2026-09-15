"""Amazon Bedrock 官方定价抓取器 —— 官方 USD per-token 价。

https://aws.amazon.com/bedrock/pricing/ 为 SSR 页面,plain HTTP 即可。
AWS 按模型厂商分区,每区有 On-Demand/Batch 表:
  Model | Input ($/1K tokens) | Output ($/1K tokens)
  注意单位是 per-1K tokens → ×1000 归一到 per-1M

只抓自有模型 Amazon Nova 系列和 Titan 系列;
其他厂商(Anthropic/Meta 等)由各厂商自己的 scraper 直采。

降级链:HTML(零 token) → 有头 Playwright
"""
from __future__ import annotations

import re
from html import unescape

from app.models.pricing import Currency, RawPrice, Region
from app.scrapers._html import extract_tables_with_context, extract_tables
from app.scrapers.base import BaseScraper, FetchLevel

_NUM = re.compile(r"\$?\s*([\d.]+)")

# Amazon 自有模型前缀
_AMAZON_PREFIXES = ("nova", "titan", "amazon")


def _price_per_1k(cell: str) -> float | None:
    """提取 per-1K-tokens 价格并 ×1000 → per-1M。"""
    if "priceof!" in cell.lower():
        return None
    m = _NUM.search(cell.replace(",", ""))
    if m:
        return round(float(m.group(1)) * 1000, 6)
    return None


def _price_per_1m(cell: str) -> float | None:
    """提取 per-1M-tokens 价格(原值)。"""
    if "priceof!" in cell.lower():
        return None
    m = _NUM.search(cell.replace(",", ""))
    if m:
        return float(m.group(1))
    return None


def _model_id(name: str) -> str:
    s = re.sub(r"\([^)]*\)", "", name).strip().lower()
    s = re.sub(r"\s+", "-", s)
    return re.sub(r"^amazon-", "", s)  # 去掉 Amazon 前缀统一口径


class AmazonBedrockScraper(BaseScraper):
    provider = "amazon"
    channel = "official"
    source_url = "https://aws.amazon.com/bedrock/pricing/"
    fetch_chain = [FetchLevel.HTML, FetchLevel.RENDER]

    async def urls(self) -> list[str]:
        return [self.source_url]

    def parse(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        seen: set[tuple[str, str]] = set()

        tables = extract_tables_with_context(text)
        # AWS 新版页面把 Nova 动态价格表放在 data-pricing-markup 属性中，
        # 外层不是标准 table；解码属性后再交给同一套表格解析器。
        for markup in re.findall(r'data-pricing-markup="([^"]+)"', text, flags=re.I):
            decoded = unescape(markup)
            for table in extract_tables(decoded):
                tables.append(("Amazon Bedrock pricing > Amazon Nova", table))

        for heading, table in tables:
            h = heading.lower()
            # 判断表的计价单位
            header_text = " ".join(table[0]).lower() if table else ""

            # 检测是否为 per-1K 还是 per-1M
            use_per_1k = "1,000" in header_text or "1k" in header_text or "/1k" in header_text
            price_fn = _price_per_1k if use_per_1k else _price_per_1m

            tier = "standard"
            if "batch" in h:
                tier = "batch"
            elif "provisioned" in h:
                continue  # 预置吞吐按小时计费,跳过

            # AWS Bedrock 的标准表第一列是 Provider，第二列才是 Model Name；
            # 个别表格则直接以模型名作为第一列。根据表头定位列，不能固定取 row[0:3]。
            header = [cell.strip().lower() for cell in (table[0] if table else [])]
            model_index = next(
                (i for i, cell in enumerate(header)
                 if "model name" in cell or cell in ("model", "模型名称")),
                0,
            )
            input_index = next(
                (i for i, cell in enumerate(header)
                 if "price per 1m input tokens" in cell or "100 万个输入 token" in cell),
                None,
            )
            output_index = next(
                (i for i, cell in enumerate(header)
                 if "price per 1m output tokens" in cell or "100 万个输出 token" in cell),
                None,
            )
            # 兼容旧的 per-1K 表和无标准表头的局部表。
            if input_index is None or output_index is None:
                input_index, output_index = 1, 2

            for row in table[1:]:
                if len(row) <= max(model_index, input_index, output_index):
                    continue
                raw = row[model_index].strip()
                if not raw or raw.lower() in ("model", "model name", "name"):
                    continue

                model = _model_id(raw)
                # 只抓 Amazon 自有模型
                if not any(model.startswith(p) for p in _AMAZON_PREFIXES):
                    continue

                key = (model, tier)
                if key in seen:
                    continue

                inp = price_fn(row[input_index])
                out = price_fn(row[output_index])
                if inp is None and out is None:
                    continue

                seen.add(key)
                results.append(RawPrice(
                    provider=self.provider,
                    channel=self.channel,
                    model=model,
                    region=Region.INTL,
                    currency=Currency.USD,
                    input_per_1m=inp,
                    output_per_1m=out,
                    service_tier=tier,
                    source_url=self.source_url,
                ))

        return results
