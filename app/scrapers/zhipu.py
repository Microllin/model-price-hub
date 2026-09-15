"""智谱 GLM 官网定价抓取器 —— 全量 API 定价（排除私有化部署）。

open.bigmodel.cn/pricing 为 SPA，需 Playwright。页面分多个区域、多 tab：

1. **旗舰模型区**
   - 文本模型 tab（默认）：GLM-5.x / GLM-4.7 / GLM-4.5-Air 等，元/百万tokens，带阶梯/缓存
   - 视觉理解 tab：GLM-5V-Turbo / GLM-4.6V 等

2. **模型推理区**（6 个 tab）
   - Language Models（默认）/ Reasoning / Multimodal / Real-time / Embedding / More
   - 价格格式多样：¥X / M Tokens、¥X / per request、¥X/10K Chats、¥X / minute

3. **搜索工具服务**：¥X / time（按次计费）
4. **知识库扩容**：¥X / GB / hour
5. **模型微调**
   - Model Training tab：¥X / 1k tokens（LoRA / Full 两列）
   - Model Inference tab：¥X / 1k tokens（Public Instance 列，排除 GPU Unit / Day 列）

排除：模型私有实例、云端私有化套餐、本地私有化

_render_text() 会依次点击所有隐藏 tab 并累积文本。
parse(text) 吃累积后的全文，可离线 fixture 单测。
"""
from __future__ import annotations

import re

from app.config import settings
from app.models.pricing import Currency, RawPrice, Region
from app.scrapers.base import BaseScraper

# ---------------------------------------------------------------------------
# 正则
# ---------------------------------------------------------------------------
# 旗舰区：模型名行
_NAME = re.compile(r"^(GLM-?[0-9][\w.\-]*)$", re.I)
# 旗舰区视觉模型也匹配
_NAME_V = re.compile(r"^(GLM-?[0-9][\w.\-V]*)$", re.I)
# 阶梯行：「输入长度 [0, 32)」「输出长度 [0, 0.2)」「输入长度 [32, 200)」
_TIER = re.compile(
    r"(输入|输出)长度\s*\[\s*([\d.]+)\s*(?:[,，]\s*([\d.]+)\s*|\+\s*)\)"
)
# 元价格行：6元 / 0.5元
_PRICE_YUAN = re.compile(r"^([\d.]+)\s*元")
# 免费行
_FREE = re.compile(r"^(免费|限时免费)$")
# 上下文窗口行：1M / 200K / 128K
_CTX_WIN = re.compile(r"^(\d+)\s*([KkMm])$")

# 推理/搜索/微调区：¥ 价格（多种格式）
_YEN_M_TOKENS = re.compile(r"¥([\d.]+)\s*/\s*M\s*Tokens", re.I)
_YEN_PER_REQ = re.compile(r"¥([\d.]+)\s*/\s*per\s*request", re.I)
_YEN_PER_TIME = re.compile(r"¥([\d.]+)\s*/\s*time", re.I)
_YEN_10K_CHATS = re.compile(r"¥([\d.]+)\s*/\s*10K\s*Chats", re.I)
_YEN_PER_MIN = re.compile(r"¥([\d.]+)\s*/\s*minute", re.I)
_YEN_GB_HOUR = re.compile(r"¥([\d.]+)\s*/\s*GB\s*/\s*hour", re.I)
_YEN_1K_TOKENS = re.compile(r"¥([\d.]+)\s*/\s*1k\s*tokens", re.I)

# 推理区通用模型名
_INFER_MODEL = re.compile(
    r"^(GLM-[\w.\-]+|Cog[\w.\-]+|CharGLM-[\w.\-]+|Emohaa|CodeGeeX-[\w.\-]+|"
    r"Rerank|Embedding-[\w.\-]+|ChatGLM[\w.\-]+|Search-[\w.\-]+|knowledge[\w_]*)$",
    re.I,
)

# Section markers
_SECTION_FLAGSHIP = "旗舰模型"
_SECTION_INFERENCE = "模型推理"
_SECTION_SEARCH = "搜索工具服务"
_SECTION_KNOWLEDGE = "知识库扩容服务"
_SECTION_FINETUNE = "模型微调"
_SECTION_PRIVATE = "模型私有实例"  # 排除


# ---------------------------------------------------------------------------
# 旗舰区阶梯 helper
# ---------------------------------------------------------------------------
def _norm_tier(m: re.Match) -> str:
    dim = "in" if m.group(1) == "输入" else "out"
    lo, hi = m.group(2), m.group(3)
    return f"{dim}:{lo}-{hi}k" if hi else f"{dim}:>{lo}k"


def _merge_tiers(tiers: list[str]) -> str:
    if len(tiers) == 1:
        t = tiers[0]
        return t[3:] if t.startswith("in:") else t
    return "+".join(tiers)


# ---------------------------------------------------------------------------
# 各 tab 的标签名
# ---------------------------------------------------------------------------
# 2026-09 页面改版:原先那 7 个 tab(视觉理解 / Reasoning models / ... )已不存在。
# 新版定价页用 button.tab-pill 做三个分区,按序号点击最稳(按文案定位会命中隐藏的导航菜单)。
_PILL_SELECTOR = "button.tab-pill"

# 卡片里的标签 → 我们关心的字段
_LABEL_INPUT = {"输入单价", "输入价格"}
_LABEL_OUTPUT = {"输出单价", "输出价格"}
_LABEL_CACHE_READ = {"缓存命中"}
_LABEL_CTX = {"上下文"}
_LABEL_UNIT_PRICE = {"单价"}
_CARD_LABELS = (
    _LABEL_INPUT | _LABEL_OUTPUT | _LABEL_CACHE_READ | _LABEL_CTX | _LABEL_UNIT_PRICE
    | {"缓存存储", "输入模态", "输出模态", "最大输出", "特点", "Batch API定价", "支持范围", "多分辨率"}
)

# 卡片里的模型名(一行一个,后面跟若干「标签/值」对)
_CARD_MODEL = re.compile(
    r"^(GLM-[\w.\-]+|CogView-[\w.\-]+|CogVideoX[\w.\-]*|Vidu[\w .\-]*|Embedding-\d+[\w.\-]*|CodeGeeX-[\w.\-]+)$",
    re.I,
)

# 价格写法 → (单位, 正则)。注意 "百万Tokens" 要排在 "/ M" 之前不会冲突:
# 前者斜杠后是「百」,后者是「M」,互不匹配。
_PRICE_FORMS = [
    ("token", re.compile(r"([\d.]+)\s*元\s*/\s*百万\s*tokens", re.I)),
    ("token", re.compile(r"([\d.]+)\s*元\s*/\s*M(?![a-z])", re.I)),
    ("10k_chars", re.compile(r"([\d.]+)\s*元\s*/\s*万\s*字符")),
    ("request", re.compile(r"([\d.]+)\s*元\s*/\s*次")),
    ("minute", re.compile(r"([\d.]+)\s*元\s*/\s*分钟")),
    # 兜底:部分卡片把单位写在列头,值里只剩「8元」。这类卡片都带「上下文」,
    # 是按 token 计费的文本模型,按 token 处理。放最后,避免抢走带单位的写法。
    ("token", re.compile(r"^([\d.]+)\s*元$")),
]
_FREE_TEXT = re.compile(r"^(限时)?免费$")


def _parse_price(value: str) -> tuple[float | None, str | None]:
    """把卡片里的价格值解析成 (数值, 计费单位)。认不出就返回 (None, None)。"""
    if not value:
        return None, None
    if _FREE_TEXT.match(value.strip()):
        return 0.0, None
    for unit, pattern in _PRICE_FORMS:
        m = pattern.search(value)
        if m:
            try:
                return float(m.group(1)), unit
            except ValueError:
                return None, None
    return None, None


class ZhipuScraper(BaseScraper):
    provider = "zhipu"
    channel = "official"
    source_url = "https://open.bigmodel.cn/pricing"
    requires_render = True

    async def fetch(self) -> list[RawPrice]:
        if not settings.use_playwright:
            return []
        text = await self._render_text(self.source_url)
        return self.parse(text)

    async def _render_text(self, url: str) -> str:
        """Playwright 渲染 + 点击所有隐藏 tab，累积各 tab 文本。"""
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            try:
                page = await browser.new_page(user_agent=settings.user_agent)
                await self._prepare_page(page)
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(5000)

                # 滚动加载所有懒加载区域
                for _ in range(10):
                    await page.mouse.wheel(0, 15000)
                    await page.wait_for_timeout(500)
                await page.wait_for_timeout(1000)

                # 逐个点击分区 pill 并累积文本。按序号点而不是按文案:
                # 页面顶部的隐藏导航菜单里也有「模型」「知识库」字样,按文案会点中它们,
                # 结果是三个分区拿到完全相同的文本(改版后曾因此只解析出 3 条)。
                texts = [await page.inner_text("body")]
                try:
                    pills = page.locator(_PILL_SELECTOR)
                    count = await pills.count()
                except Exception:
                    count = 0
                for index in range(count):
                    try:
                        await pills.nth(index).click(force=True)
                        await page.wait_for_timeout(2500)
                        texts.append(await page.inner_text("body"))
                    except Exception:
                        continue

                return "\n".join(texts)
            finally:
                await browser.close()

    # ==================================================================
    # parse：统一入口
    # ==================================================================
    def parse(self, text: str) -> list[RawPrice]:
        """优先按新版卡片布局解析;拿不到再退回旧版表格逻辑。

        2026-09 定价页改版:价格从表格改成「模型名 + 若干标签/值」的卡片,
        旧解析器只认得旗舰区那几行,22 条掉到 3 条。卡片解析覆盖全部分区;
        保留旧逻辑是为了万一页面回滚或还有残留表格时不至于一条都抓不到。
        """
        rows = self._parse_cards(text)
        return rows if rows else self._parse_legacy(text)

    def _parse_cards(self, text: str) -> list[RawPrice]:
        """解析新版卡片:模型名独占一行,其后是「标签」「值」交替的行。"""
        lines = [ln.strip() for ln in text.splitlines()]
        lines = [ln for ln in lines if ln]
        results: list[RawPrice] = []
        seen: set[tuple] = set()

        index = 0
        while index < len(lines):
            match = _CARD_MODEL.match(lines[index])
            if not match:
                index += 1
                continue
            model = match.group(1)
            index += 1
            fields: dict[str, str] = {}
            # 收集到下一个模型名为止
            while index < len(lines) and not _CARD_MODEL.match(lines[index]):
                label = lines[index]
                if label in _CARD_LABELS and index + 1 < len(lines):
                    value = lines[index + 1]
                    if value not in _CARD_LABELS and not _CARD_MODEL.match(value):
                        fields.setdefault(label, value)
                        index += 2
                        continue
                index += 1

            row = self._card_to_price(model, fields)
            if row is not None and row.key() not in seen:
                seen.add(row.key())
                results.append(row)
        return results

    def _card_to_price(self, model: str, fields: dict[str, str]) -> RawPrice | None:
        """把一张卡片的标签/值组装成 RawPrice;没有任何价格就返回 None。"""
        unit: str | None = None
        values: dict[str, float] = {}
        for labels, key in (
            (_LABEL_INPUT, "input"),
            (_LABEL_OUTPUT, "output"),
            (_LABEL_CACHE_READ, "cache_read"),
            (_LABEL_UNIT_PRICE, "unit_price"),
        ):
            for label in labels:
                if label in fields:
                    price, price_unit = _parse_price(fields[label])
                    if price is not None:
                        values[key] = price
                        unit = unit or price_unit
                    break

        # 「单价」型卡片(如 TTS 按万字符计)只有一个价,记为输入价
        if "input" not in values and "unit_price" in values:
            values["input"] = values.pop("unit_price")
        values.pop("unit_price", None)
        if not values:
            return None

        ctx_win = None
        for label in _LABEL_CTX:
            if label in fields:
                cw = _CTX_WIN.match(fields[label])
                if cw:
                    val = int(cw.group(1))
                    ctx_win = val * 1000 if cw.group(2).upper() == "K" else val * 1_000_000
                break

        return RawPrice(
            provider="zhipu",
            channel="official",
            model=model,
            region=Region.CN,
            currency=Currency.CNY,
            input_per_1m=values.get("input"),
            output_per_1m=values.get("output"),
            cached_input_per_1m=values.get("cache_read"),
            context_window=ctx_win,
            billing_unit=unit or "token",
            source_url=self.source_url,
        )

    def _parse_legacy(self, text: str) -> list[RawPrice]:
        results: list[RawPrice] = []
        seen: set[tuple] = set()

        def _add(row: RawPrice) -> None:
            k = row.key()
            if k not in seen:
                seen.add(k)
                results.append(row)

        for r in self._parse_flagship(text):
            _add(r)
        for r in self._parse_inference(text):
            _add(r)
        for r in self._parse_search(text):
            _add(r)
        for r in self._parse_knowledge(text):
            _add(r)
        for r in self._parse_finetune(text):
            _add(r)
        return results

    # ==================================================================
    # 1. 旗舰模型区（文本模型 + 视觉理解 tab）
    # ==================================================================
    def _parse_flagship(self, text: str) -> list[RawPrice]:
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        results: dict[tuple[str, str | None, str], RawPrice] = {}

        def emit(
            name: str,
            tier: str | None,
            prices: list[float],
            cached: float | None,
            ctx_win: int | None,
            is_free: bool,
            modality: str,
        ) -> None:
            key = (name, tier, modality)
            if key in results:
                return
            if is_free:
                results[key] = RawPrice(
                    provider=self.provider, channel=self.channel, model=name,
                    region=Region.CN, currency=Currency.CNY,
                    input_per_1m=0.0, output_per_1m=0.0, cached_input_per_1m=0.0,
                    context_window=ctx_win, context_range=tier,
                    modality=modality, source_url=self.source_url,
                )
                return
            if len(prices) < 2 or (prices[0] == 0 and prices[1] == 0):
                return
            results[key] = RawPrice(
                provider=self.provider, channel=self.channel, model=name,
                region=Region.CN, currency=Currency.CNY,
                input_per_1m=prices[0], output_per_1m=prices[1],
                cached_input_per_1m=cached,
                context_window=ctx_win, context_range=tier,
                modality=modality, source_url=self.source_url,
            )

        # 旗舰区起止：从「旗舰模型」到「模型推理」
        flagship_start = None
        inference_start = None
        for idx, ln in enumerate(lines):
            if _SECTION_FLAGSHIP in ln and flagship_start is None:
                flagship_start = idx
            if _SECTION_INFERENCE in ln and flagship_start is not None:
                inference_start = idx
                break
        if flagship_start is None:
            return []
        scope = lines[flagship_start : (inference_start or len(lines))]

        # 检测模态：文本模型 tab 内为 "text"，视觉理解 tab 内为 "multimodal"
        modality = "text"

        i = 0
        while i < len(scope):
            line = scope[i]
            # 切换模态标记
            if line == "视觉理解" and i > 0 and "文本模型" in scope[max(0, i - 3) : i + 1]:
                i += 1
                continue
            # 当出现视觉理解描述或视觉模型名时切换
            if "GLM-5V" in line or "GLM-4.6V" in line or "GLM-4.5V" in line:
                modality = "multimodal"

            m = _NAME_V.match(line)
            if not m:
                i += 1
                continue
            name = m.group(1)

            tier_stack: list[str] = []
            prices: list[float] = []
            cached: float | None = None
            ctx_win: int | None = None
            is_free = False

            j = i + 1
            while j < len(scope) and j < i + 50:
                nxt = scope[j]
                if _NAME_V.match(nxt) and nxt != name:
                    break
                if nxt in ("新品",) or "图片" in nxt or "文件" in nxt or "文本" in nxt:
                    j += 1
                    continue
                # 跳过 "/" 行（视觉理解 tab 用 / 表示无上下文限制）
                if nxt == "/":
                    j += 1
                    continue
                cw = _CTX_WIN.match(nxt)
                if cw:
                    val = int(cw.group(1))
                    unit = cw.group(2).upper()
                    ctx_win = val * 1000 if unit == "K" else val * 1_000_000
                    j += 1
                    continue
                tm = _TIER.search(nxt)
                if tm:
                    if prices or is_free:
                        tier_key = _merge_tiers(tier_stack) if tier_stack else None
                        emit(name, tier_key, prices, cached, ctx_win, is_free, modality)
                    new_tier = _norm_tier(tm)
                    dim = new_tier.split(":")[0]
                    existing_dims = [t.split(":")[0] for t in tier_stack]
                    if dim in existing_dims:
                        tier_stack = [new_tier]
                    else:
                        tier_stack.append(new_tier)
                    prices, cached, is_free = [], None, False
                    j += 1
                    continue
                fm = _FREE.match(nxt)
                if fm:
                    if nxt == "免费" and len(prices) < 2:
                        prices.append(0.0)
                        if len(prices) == 2:
                            is_free = True
                    j += 1
                    continue
                pm = _PRICE_YUAN.match(nxt)
                if pm:
                    val = float(pm.group(1))
                    if len(prices) < 2:
                        prices.append(val)
                    elif cached is None:
                        cached = val
                    j += 1
                    continue
                j += 1

            if prices or is_free:
                tier_key = _merge_tiers(tier_stack) if tier_stack else None
                emit(name, tier_key, prices, cached, ctx_win, is_free, modality)
            i = j

        return list(results.values())

    # ==================================================================
    # 2. 模型推理区（所有 tab：Language / Reasoning / Multimodal / Real-time / Embedding / More）
    # ==================================================================
    def _parse_inference(self, text: str) -> list[RawPrice]:
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        results: dict[str, RawPrice] = {}

        # 已在旗舰区解析过的模型名
        flagship_models = {r.model for r in self._parse_flagship(text)}

        i = 0
        while i < len(lines):
            line = lines[i]
            nm = _INFER_MODEL.match(line)
            if not nm:
                i += 1
                continue
            model_name = nm.group(1)
            if model_name in flagship_models or model_name in results:
                i += 1
                continue
            # 排除搜索/知识库/微调区（由独立方法处理）
            if model_name.startswith("Search-") or model_name.startswith("knowledge"):
                i += 1
                continue

            # 向后扫描找价格
            for j in range(i + 1, min(i + 8, len(lines))):
                nxt = lines[j]
                # ¥X / M Tokens
                pm = _YEN_M_TOKENS.search(nxt)
                if pm:
                    price = float(pm.group(1))
                    # 查找 Batch API 价格（下一行或同行）
                    batch_price = None
                    for k in range(j + 1, min(j + 3, len(lines))):
                        bm = _YEN_M_TOKENS.search(lines[k])
                        if bm:
                            batch_price = float(bm.group(1))
                            break
                        if "Not Supported" in lines[k]:
                            break
                    results[model_name] = RawPrice(
                        provider=self.provider, channel=self.channel,
                        model=model_name, region=Region.CN, currency=Currency.CNY,
                        input_per_1m=price, output_per_1m=price,
                        source_url=self.source_url,
                    )
                    break
                # ¥X / per request
                pm = _YEN_PER_REQ.search(nxt)
                if pm:
                    price = float(pm.group(1))
                    results[model_name] = RawPrice(
                        provider=self.provider, channel=self.channel,
                        model=model_name, region=Region.CN, currency=Currency.CNY,
                        input_per_1m=price, output_per_1m=None,
                        billing_unit="request",
                        source_url=self.source_url,
                    )
                    break
                # ¥X/10K Chats
                pm = _YEN_10K_CHATS.search(nxt)
                if pm:
                    price = float(pm.group(1))
                    results[model_name] = RawPrice(
                        provider=self.provider, channel=self.channel,
                        model=model_name, region=Region.CN, currency=Currency.CNY,
                        input_per_1m=price, output_per_1m=None,
                        billing_unit="10k_chats",
                        source_url=self.source_url,
                    )
                    break
                # ¥X / minute
                pm = _YEN_PER_MIN.search(nxt)
                if pm:
                    price = float(pm.group(1))
                    results[model_name] = RawPrice(
                        provider=self.provider, channel=self.channel,
                        model=model_name, region=Region.CN, currency=Currency.CNY,
                        input_per_1m=price, output_per_1m=None,
                        billing_unit="minute",
                        source_url=self.source_url,
                    )
                    break
            i += 1

        return list(results.values())

    # ==================================================================
    # 3. 搜索工具服务（¥X / time）
    # ==================================================================
    def _parse_search(self, text: str) -> list[RawPrice]:
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        results: dict[str, RawPrice] = {}

        for i, line in enumerate(lines):
            if not line.startswith("Search-"):
                continue
            if line in results:
                continue
            for j in range(i + 1, min(i + 5, len(lines))):
                pm = _YEN_PER_TIME.search(lines[j])
                if pm:
                    price = float(pm.group(1))
                    results[line] = RawPrice(
                        provider=self.provider, channel=self.channel,
                        model=line, region=Region.CN, currency=Currency.CNY,
                        input_per_1m=price, output_per_1m=None,
                        billing_unit="request",
                        modality="search",
                        source_url=self.source_url,
                    )
                    break

        return list(results.values())

    # ==================================================================
    # 4. 知识库扩容（¥X / GB / hour）
    # ==================================================================
    def _parse_knowledge(self, text: str) -> list[RawPrice]:
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        results: list[RawPrice] = []

        for i, line in enumerate(lines):
            if not line.startswith("knowledge"):
                continue
            for j in range(i + 1, min(i + 5, len(lines))):
                pm = _YEN_GB_HOUR.search(lines[j])
                if pm:
                    price = float(pm.group(1))
                    results.append(RawPrice(
                        provider=self.provider, channel=self.channel,
                        model=line, region=Region.CN, currency=Currency.CNY,
                        input_per_1m=price, output_per_1m=None,
                        billing_unit="gb_hour",
                        modality="storage",
                        source_url=self.source_url,
                    ))
                    break

        return results

    # ==================================================================
    # 5. 模型微调（Training + Inference 的 Public Instance 列）
    # ==================================================================
    def _parse_finetune(self, text: str) -> list[RawPrice]:
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        results: dict[tuple[str, str], RawPrice] = {}

        # 找所有「模型微调」区域
        finetune_sections: list[tuple[int, int]] = []
        for i, ln in enumerate(lines):
            if _SECTION_FINETUNE in ln:
                # 找到区域终止
                end = len(lines)
                for k in range(i + 1, len(lines)):
                    if _SECTION_PRIVATE in lines[k]:
                        end = k
                        break
                finetune_sections.append((i, end))

        for start, end in finetune_sections:
            scope = lines[start:end]
            # 判断是 Training tab 还是 Inference tab
            is_training = "Pricing with LoRA" in "\n".join(scope[:10]) or "Training Version" in "\n".join(scope[:10])
            is_inference = "Public Instance" in "\n".join(scope[:10]) or "Deployment Version" in "\n".join(scope[:10])

            channel_suffix = "training" if is_training else "finetune-inference"

            for si, sline in enumerate(scope):
                nm = _INFER_MODEL.match(sline)
                if not nm:
                    continue
                model_name = nm.group(1)
                deployment_version = next(
                    (scope[k].strip() for k in range(si + 1, min(si + 4, len(scope)))
                     if re.fullmatch(r"[0-9]+[KkMm](?:-[A-Za-z0-9]+)?", scope[k].strip())),
                    None,
                )

                if is_training:
                    # Training tab 每行有两列：Pricing with LoRA | Pricing with Full
                    # 每列要么是「¥X / 1k tokens」要么是「Not Supported」
                    # 逐格扫描，按出现顺序判定 LoRA→Full
                    slots: list[float | None] = []  # [lora_price, full_price]
                    for sj in range(si + 1, min(si + 6, len(scope))):
                        snxt = scope[sj]
                        if _INFER_MODEL.match(snxt):
                            break
                        pm = _YEN_1K_TOKENS.search(snxt)
                        if pm and len(slots) < 2:
                            slots.append(float(pm.group(1)))
                        elif "Not Supported" in snxt and len(slots) < 2:
                            slots.append(None)
                    # slots[0]=LoRA, slots[1]=Full
                    for idx, method in enumerate(["lora", "full"]):
                        if idx < len(slots) and slots[idx] is not None:
                            price_per_1m = slots[idx] * 1000  # 1k → 1M
                            key = (model_name, method)
                            if key not in results:
                                results[key] = RawPrice(
                                    provider=self.provider, channel=self.channel,
                                    model=model_name, region=Region.CN, currency=Currency.CNY,
                                    input_per_1m=price_per_1m, output_per_1m=None,
                                    billing_unit="token",
                                    modality="training",
                                    service_tier=method,
                                    deployment_version=deployment_version,
                                    source_url=self.source_url,
                                )

                elif is_inference:
                    # Inference tab：Public Instance | Private Instance
                    # 只取 Public Instance（¥X / 1k tokens），跳过 Private（¥X / GPU Unit / Day）
                    for sj in range(si + 1, min(si + 6, len(scope))):
                        snxt = scope[sj]
                        if _INFER_MODEL.match(snxt):
                            break
                        if "GPU Unit" in snxt:
                            continue
                        pm = _YEN_1K_TOKENS.search(snxt)
                        if pm:
                            price_per_1m = float(pm.group(1)) * 1000
                            key = (model_name, "finetune-inference")
                            if key not in results:
                                results[key] = RawPrice(
                                    provider=self.provider, channel=self.channel,
                                    model=model_name, region=Region.CN, currency=Currency.CNY,
                                    input_per_1m=price_per_1m, output_per_1m=None,
                                    billing_unit="token",
                                    modality="finetune-inference",
                                    deployment_version=deployment_version,
                                    source_url=self.source_url,
                                )
                            break

        return list(results.values())
