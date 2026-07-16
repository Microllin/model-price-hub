"""视觉抓取器基类 —— 截图 + Claude 多模态结构化提取,官方定价的"一劳永逸"路径。

流程:Playwright 渲染页面(可点开各模型 tab / 裁剪定价区)→ 逐张截图 →
Claude(默认 Haiku 4.5)在结构化输出约束下把图里的定价表吐成 JSON → 单位归一 →
产出 RawPrice。多源交叉验证层负责兜住模型偶发看错的数字。

为什么用视觉:各厂商官方定价页结构千差万别,有的价格用特殊组件/canvas 渲染,
inner_text 读不到(如 Kimi);视觉直接读像素,对页面改版和反爬都更鲁棒。加新官方厂商
只需继承本类、给一个 URL(+ 可选点哪些 tab / 裁哪块),无需再写解析正则。

依赖:`pip install .[vision] && playwright install chromium`,并设 MPH_ANTHROPIC_API_KEY。
缺 key 或未启用 Playwright 时,fetch() 优雅返回空(不影响其它抓取器)。
"""
from __future__ import annotations

import base64
import json
import os

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper


def _has_credentials() -> bool:
    """视觉提取是否有可用凭据:MPH_ANTHROPIC_API_KEY,或标准 ANTHROPIC_AUTH_TOKEN /
    ANTHROPIC_API_KEY 环境变量(后者兼容自定义网关 + Bearer token)。"""
    return bool(
        settings.anthropic_api_key
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or os.environ.get("ANTHROPIC_API_KEY")
    )


def _client():
    """构造 Anthropic 客户端。显式 MPH_ANTHROPIC_API_KEY 优先;否则由 SDK 从环境读取
    ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY 与 ANTHROPIC_BASE_URL(支持自定义网关)。"""
    import anthropic

    if settings.anthropic_api_key:
        return anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return anthropic.Anthropic()

# 结构化输出 schema:强制模型按此吐 JSON(Haiku/Sonnet/Opus 均支持)
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rows"],
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["model", "input_price", "output_price", "unit"],
                "properties": {
                    "model": {"type": "string"},
                    "input_price": {"type": "number"},
                    "output_price": {"type": "number"},
                    "cache_read_price": {"type": ["number", "null"]},
                    "unit": {"type": "string", "enum": ["per_1m", "per_1k"]},
                    "context_window": {"type": ["integer", "null"]},
                },
            },
        }
    },
}

_PROMPT = (
    "这是一张网页截图,可能是也可能不是大模型厂商的官方定价页。\n"
    "【最重要】只转写图片里**肉眼可见的、明确标注了价格数字和货币/单位的定价表格**。"
    "如果这张图里没有这样的定价表格(比如它是文档页、快速开始、代码示例、功能介绍),"
    "就返回 {\"rows\": []},绝对不要凭你已知的任何模型价格去填——没看见就是空。\n"
    "【只要按 token 计费的推理/API 定价】单位必须是每百万 tokens(per_1m)或每千 tokens(per_1k)。"
    "**必须排除**以下非 token 计费的表格,一行都不要:微调/训练价、私有化部署价、"
    "算力资源包、按「元/算力单元/天」「元/小时」计费、按张/按秒/按次计费。单位不是「每 X tokens」的一律跳过。\n"
    "提取规则(仅当确有 token 定价表时):\n"
    "- 只要文本/对话/推理/代码类模型;跳过图像、视频、音频、语音、向量、重排模型。\n"
    "- 同一模型若按上下文长度分多档,用**基础模型名**(如 GLM-5.1,不要写成 GLM-5.1(输入长度[0,32])),"
    "取标准档/第一档的输入输出价。\n"
    "- input_price / output_price 逐字照抄图里的数字,unit 如实标注 per_1m 或 per_1k,不要自行换算。\n"
    "- 缓存命中/缓存读取价填到 cache_read_price,它是同一模型的一个价格列,**不是单独一行模型**。\n"
    "- 若标了折扣价/限时价,用当前实际生效价。\n"
    "- 任何看不清或需要猜测的数字,宁可整行不填。\n"
    "只返回 JSON,不要解释。"
)


class VisionScraper(BaseScraper):
    """截图 + 视觉提取的抓取器基类。

    子类需声明:provider、source_url、region、currency;可选:tab_selectors(要依次点开
    并各截一图的文本选择器)、full_page、model_id_map(把截图里的展示名归一到规范 id)。
    """

    requires_render = True
    region: Region = Region.CN
    currency: Currency = Currency.CNY
    tab_selectors: list[str] = []       # 空 = 不点击;非空 = 逐个点击后各截一批
    screenshot_urls: list[str] = []     # 空 = 只截 source_url;非空 = 逐个 URL 各截一批
    max_shots_per_page: int = 10        # 每页最多滚动截几张视口图(重叠 ~40%)

    @property
    def source_name(self) -> str:
        """视觉源统一前缀 vision-,便于置信度聚合区分"视觉主源"与正则验证器。"""
        return f"vision-{self.provider}"

    # parse 不用于视觉抓取器(数据来自图片而非文本),但基类是抽象方法,需给个实现
    def parse(self, text: str) -> list[RawPrice]:  # pragma: no cover
        return []

    async def fetch(self) -> list[RawPrice]:
        if not settings.use_playwright:
            return []
        if not _has_credentials():
            print(f"  [skip] {self.__class__.__name__}: 无视觉凭据(设 ANTHROPIC_AUTH_TOKEN 或 MPH_ANTHROPIC_API_KEY),跳过")
            return []
        shots = await self._capture()
        results: dict[str, RawPrice] = {}
        for png in shots:
            for r in self._extract(png):
                results[r.model] = r  # 同名去重,后截的覆盖
        return list(results.values())

    # ---- 截图 ----
    async def _capture(self) -> list[bytes]:
        """滚动分段截【视口】图(不用全页截图——长页会卡在字体加载/被压糊)。
        screenshot_urls 非空时逐个 URL;否则截 source_url + 可选 tab 点击。"""
        from playwright.async_api import async_playwright

        shots: list[bytes] = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = await browser.new_page(
                    viewport={"width": 1200, "height": 1050}, user_agent=settings.user_agent
                )
                urls = self.screenshot_urls or [self.source_url]
                for url in urls:
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        await page.wait_for_timeout(6000)
                    except Exception:
                        continue
                    for sel in self.tab_selectors or [None]:
                        if sel:
                            try:
                                await page.click(f"text={sel}", timeout=6000)
                                await page.wait_for_timeout(2500)
                            except Exception:
                                pass
                        shots.extend(await self._scroll_shots(page))
            finally:
                await browser.close()
        return shots

    async def _scroll_shots(self, page) -> list[bytes]:
        out: list[bytes] = []
        height = await page.evaluate("document.body.scrollHeight")
        y = 0
        while y < max(height, 1) and len(out) < self.max_shots_per_page:
            await page.evaluate(f"window.scrollTo(0,{y})")
            await page.wait_for_timeout(500)
            try:
                out.append(await page.screenshot(timeout=15000))
            except Exception:
                break
            y += 640  # < 视口高(1050),块间重叠 ~40%,避免定价表被切在两块之间
        return out

    # ---- 视觉提取 ----
    def _extract(self, png: bytes) -> list[RawPrice]:
        client = _client()
        b64 = base64.standard_b64encode(png).decode("ascii")
        resp = client.messages.create(
            model=settings.vision_model,
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": b64},
                        },
                        {"type": "text", "text": _PROMPT},
                    ],
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return self.rows_to_prices(text)

    # ---- 归一(与网络/API 分离,可离线单测)----
    def rows_to_prices(self, model_json: str) -> list[RawPrice]:
        try:
            rows = json.loads(model_json).get("rows", [])
        except (ValueError, AttributeError):
            return []
        out: list[RawPrice] = []
        for row in rows:
            model = str(row.get("model", "")).strip()
            inp = row.get("input_price")
            outp = row.get("output_price")
            if not model or (inp is None and outp is None):
                continue
            mult = 1000 if row.get("unit") == "per_1k" else 1  # per_1k → per_1m
            out.append(
                RawPrice(
                    provider=self.map_provider(model),
                    channel=self.channel,
                    model=self.map_model(model),
                    region=self.region,
                    currency=self.currency,
                    input_per_1m=self._num(inp, mult),
                    output_per_1m=self._num(outp, mult),
                    cached_input_per_1m=self._num(row.get("cache_read_price"), mult),
                    context_window=row.get("context_window"),
                    source_url=self.source_url,
                )
            )
        return out

    @staticmethod
    def _num(v, mult) -> float | None:
        if v is None:
            return None
        try:
            return round(float(v) * mult, 6)
        except (TypeError, ValueError):
            return None

    # 子类可覆盖:把截图里的展示名映射到规范 id / provider
    def map_model(self, shown: str) -> str:
        return shown

    def map_provider(self, shown: str) -> str:
        return self.provider
