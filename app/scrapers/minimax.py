"""MiniMax 开放平台官网定价抓取器 —— 全量 API 定价。

platform.minimaxi.com/docs/guides/pricing-paygo 为 SPA，需 Playwright。

覆盖区域：
1. 语言模型（元/百万 tokens）：MiniMax-M3（阶梯）/ M2.7 / M2.7-highspeed
2. 语音合成（元/万字符）：speech-2.8-hd / speech-2.8-turbo
3. 语音-音色设计/复刻（元/音色）：Voice Design / Voice Cloning
4. 视频生成（元/秒）：MiniMax-H3 / H3-Regeneration
5. 视频-H3-Context-IR（元/百万 tokens）
6. 图像（元/张）：image-01 / image-01-live
7. MCP（元/次）：API-vlm
8. 服务端工具（元/次）：web_search

排除：已下线音乐模型、视频-输入素材价格（免费部分）

parse(text) 只吃渲染后文本，可离线 fixture 单测。
"""
from __future__ import annotations

import re

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

_PRICE_CELL = re.compile(r"^[\d.\s]+$")
# 阶梯/促销标签格:"≤ 512k 输入 tokens 永久五折" / "> 512k 输入 tokens"
_TIER_LABEL = re.compile(r"(≤|>=?|＞)\s*(\d+(?:\.\d+)?\s*[KkMm])")

# 语音价格：3.5 / 2 元/万字符
_TTS_PRICE = re.compile(r"([\d.]+)$")
# 视频价格：0.80 元/秒
_VIDEO_PRICE = re.compile(r"([\d.]+)\s*元/秒")
# 图像/MCP/搜索价格：0.025 元/张 or 0.03 元/次
_UNIT_PRICE = re.compile(r"([\d.]+)$")
# H3-Context-IR: 5.80 元/百万 tokens
_M_TOKENS_PRICE = re.compile(r"([\d.]+)\s*元/百万\s*tokens")


def _tier_of(cell: str) -> str | None:
    m = _TIER_LABEL.search(cell)
    if not m:
        return None
    raw = m.group(2).replace(" ", "")
    n = float(re.match(r"[\d.]+", raw).group())
    if raw.lower().endswith("m"):
        n *= 1000
    bound = f"{int(n)}k" if n == int(n) else f"{n}k"
    return f">{bound}" if m.group(1).startswith((">", "＞")) else f"0-{bound}"


class MiniMaxScraper(BaseScraper):
    provider = "minimax"
    channel = "official"
    source_url = "https://platform.minimaxi.com/docs/guides/pricing-paygo"
    requires_render = True

    async def fetch(self) -> list[RawPrice]:
        if not settings.use_playwright:
            return []
        text = await self._render_text(self.source_url)
        return self.parse(text)

    async def _render_text(self, url: str) -> str:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
            try:
                page = await browser.new_page(user_agent=settings.user_agent)
                await self._prepare_page(page)
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3500)
                for _ in range(6):
                    await page.mouse.wheel(0, 15000)
                    await page.wait_for_timeout(600)
                return await page.inner_text("body")
            finally:
                await browser.close()

    def parse(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        seen: set[tuple] = set()

        def _add(row: RawPrice) -> None:
            k = (row.model, row.context_range, row.modality, row.billing_unit)
            if k not in seen:
                seen.add(k)
                results.append(row)

        for r in self._parse_language(text):
            _add(r)
        for r in self._parse_tts(text):
            _add(r)
        for r in self._parse_voice_design(text):
            _add(r)
        for r in self._parse_video(text):
            _add(r)
        for r in self._parse_image(text):
            _add(r)
        for r in self._parse_mcp(text):
            _add(r)
        for r in self._parse_tools(text):
            _add(r)
        return results

    # 1. 语言模型（原有逻辑）
    def _parse_language(self, text: str) -> list[RawPrice]:
        _SKIP_LANG = ("视频", "图生", "文生", "音乐", "语音", "图片", "hailuo", "speech", "voice")
        results: dict[tuple[str, str | None], RawPrice] = {}
        for line in text.split("\n"):
            if not line.strip().startswith("MiniMax-"):
                continue
            low = line.lower()
            if any(k in low for k in _SKIP_LANG):
                continue
            cells = [c.strip() for c in line.split("\t")]
            name = cells[0].strip()
            tier = _tier_of(cells[1]) if len(cells) > 1 else None
            if len(cells) > 1 and tier is None and not _PRICE_CELL.match(cells[1]):
                continue
            key = (name, tier)
            if key in results:
                continue
            price_cells = [c for c in cells[1:] if _PRICE_CELL.match(c) and re.search(r"\d", c)]
            if len(price_cells) < 2:
                continue
            inp = float(re.findall(r"[\d.]+", price_cells[0])[-1])
            out = float(re.findall(r"[\d.]+", price_cells[1])[-1])
            cache = float(re.findall(r"[\d.]+", price_cells[2])[-1]) if len(price_cells) >= 3 else None
            if inp == 0 and out == 0:
                continue
            results[key] = RawPrice(
                provider=self.provider, channel=self.channel,
                model=name, region=Region.CN, currency=Currency.CNY,
                input_per_1m=inp, output_per_1m=out,
                cached_input_per_1m=cache, context_range=tier,
                source_url=self.source_url,
            )
        return list(results.values())

    # 2. 语音合成（元/万字符）
    def _parse_tts(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        seen: set[str] = set()
        for line in text.split("\n"):
            if "speech-" not in line:
                continue
            cells = [c.strip() for c in line.split("\t")]
            model = None
            price = None
            for c in cells:
                if c.startswith("speech-"):
                    model = c
                m = _TTS_PRICE.match(c)
                if m and float(m.group(1)) > 0:
                    price = float(m.group(1))
            if model and price and model not in seen:
                seen.add(model)
                results.append(RawPrice(
                    provider=self.provider, channel=self.channel,
                    model=model, region=Region.CN, currency=Currency.CNY,
                    input_per_1m=price, output_per_1m=None,
                    billing_unit="10k_chars", modality="tts",
                    source_url=self.source_url,
                ))
        return results

    # 3. 音色设计/复刻（元/音色）
    def _parse_voice_design(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            if "Voice Design" not in line and "Voice Cloning" not in line:
                continue
            name = "voice-design" if "Design" in line else "voice-cloning"
            for j in range(i, min(i + 5, len(lines))):
                m = re.search(r"([\d.]+)", lines[j])
                if m and float(m.group(1)) > 0:
                    results.append(RawPrice(
                        provider=self.provider, channel=self.channel,
                        model=name, region=Region.CN, currency=Currency.CNY,
                        input_per_1m=float(m.group(1)), output_per_1m=None,
                        billing_unit="per_voice", modality="tts",
                        source_url=self.source_url,
                    ))
                    break
        return results

    # 4. 视频生成（元/秒 + H3-Context-IR 元/百万 tokens）
    def _parse_video(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        seen_video: set[str] = set()
        for i, line in enumerate(lines):
            if "MiniMax-H3" not in line:
                continue
            model = line.split("\t")[0].strip() if "\t" in line else line

            # H3-Context-IR：百万 tokens 计费
            if "Context-IR" in line:
                # 找输入输出价格
                for j in range(i, min(i + 5, len(lines))):
                    prices = _M_TOKENS_PRICE.findall(lines[j])
                    if len(prices) >= 2:
                        results.append(RawPrice(
                            provider=self.provider, channel=self.channel,
                            model="MiniMax-H3-Context-IR",
                            region=Region.CN, currency=Currency.CNY,
                            input_per_1m=float(prices[0]), output_per_1m=float(prices[1]),
                            modality="video", source_url=self.source_url,
                        ))
                        break
                continue

            # H3 / H3-Regeneration：元/秒
            for j in range(i, min(i + 5, len(lines))):
                m = _VIDEO_PRICE.search(lines[j])
                if m:
                    price = float(m.group(1))
                    # 分辨率从前面行提取
                    res = ""
                    for k in range(max(0, j - 2), j + 1):
                        if "2K" in lines[k]:
                            res = "-2k"
                        elif "768P" in lines[k] or "768p" in lines[k]:
                            res = "-768p"
                    full_model = f"{model}{res}"
                    if full_model not in seen_video:
                        seen_video.add(full_model)
                        results.append(RawPrice(
                            provider=self.provider, channel=self.channel,
                            model=full_model, region=Region.CN, currency=Currency.CNY,
                            input_per_1m=price, output_per_1m=None,
                            billing_unit="second", modality="video",
                            source_url=self.source_url,
                        ))
        return results

    # 5. 图像（元/张）
    def _parse_image(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        for line in text.split("\n"):
            if "image-" not in line:
                continue
            cells = [c.strip() for c in line.split("\t")]
            model = None
            price = None
            for c in cells:
                if c.startswith("image-"):
                    model = c
                m = _UNIT_PRICE.match(c)
                if m and float(m.group(1)) > 0:
                    price = float(m.group(1))
            if model and price:
                results.append(RawPrice(
                    provider=self.provider, channel=self.channel,
                    model=model, region=Region.CN, currency=Currency.CNY,
                    input_per_1m=price, output_per_1m=None,
                    billing_unit="per_image", modality="image",
                    source_url=self.source_url,
                ))
        return results

    # 6. MCP（元/次）
    def _parse_mcp(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        for line in text.split("\n"):
            if "API-vlm" not in line:
                continue
            cells = [c.strip() for c in line.split("\t")]
            price = None
            for c in cells:
                m = _UNIT_PRICE.match(c)
                if m and float(m.group(1)) > 0:
                    price = float(m.group(1))
            if price:
                results.append(RawPrice(
                    provider=self.provider, channel=self.channel,
                    model="API-vlm", region=Region.CN, currency=Currency.CNY,
                    input_per_1m=price, output_per_1m=None,
                    billing_unit="request", modality="mcp",
                    source_url=self.source_url,
                ))
                break
        return results

    # 7. 服务端工具（元/次）
    def _parse_tools(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        for line in text.split("\n"):
            if "web_search" not in line:
                continue
            cells = [c.strip() for c in line.split("\t")]
            if cells[0] != "web_search":
                continue
            price = None
            for c in cells:
                m = _UNIT_PRICE.match(c)
                if m and float(m.group(1)) > 0:
                    price = float(m.group(1))
            if price:
                results.append(RawPrice(
                    provider=self.provider, channel=self.channel,
                    model="web_search", region=Region.CN, currency=Currency.CNY,
                    input_per_1m=price, output_per_1m=None,
                    billing_unit="request", modality="search",
                    source_url=self.source_url,
                ))
                break
        return results
