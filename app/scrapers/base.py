"""抓取器框架:BaseScraper 抽象类,提供四级降级取数方式。

降级链（每一级失败或结果为空时自动降到下一级）:
  Level 1: JSON API — 纯接口,零 token 消耗,最快最稳
  Level 2: HTTP GET + HTML 正则/表格解析 — 零 token 消耗
  Level 3: Playwright 有头渲染 + 正则解析 — SPA 页面,零 token 但需浏览器
  Level 4: Playwright 截图 + Claude 视觉 OCR — 兜底,消耗 API token

子类只需实现对应级别的方法,未实现的级别会自动跳过。
"""
from __future__ import annotations

import abc
import asyncio
import sys

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.models.pricing import RawPrice


class FetchLevel:
    """爬取层级常量。"""
    API = "api"                # Level 1: 纯 JSON API
    HTML = "html"              # Level 2: HTTP GET + HTML 解析
    RENDER = "render"          # Level 3: Playwright 有头渲染 + 文本/HTML 解析
    VISION = "vision"          # Level 4: Playwright 截图 + Claude 视觉 OCR


class BaseScraper(abc.ABC):
    """所有抓取器的基类。

    子类需声明 provider/source_url,并实现 parse(html_or_json) 返回 RawPrice 列表。
    默认走 HTTP GET;若页面为 JS 重渲染,设 requires_render=True 走 Playwright。
    parse 与网络分离,便于用离线 fixture 做单测。

    四级降级链:
      子类可声明 fetch_chain = [FetchLevel.API, FetchLevel.HTML, FetchLevel.RENDER, FetchLevel.VISION]
      或只保留需要的级别。默认行为保持向后兼容(requires_render → render 优先)。
    """

    provider: str
    channel: str = "official"
    source_url: str
    requires_render: bool = False

    # 子类可覆盖:指定该抓取器的降级链(按优先级排列)
    # 未设置时由 _default_chain() 根据 requires_render 推导
    fetch_chain: list[str] | None = None

    @property
    def source_name(self) -> str:
        """数据源标识,用于多源交叉验证。默认取类名去掉 Scraper 后缀。"""
        return self.__class__.__name__.replace("Scraper", "").lower()

    @property
    def _effective_chain(self) -> list[str]:
        """实际生效的降级链。"""
        if self.fetch_chain is not None:
            return self.fetch_chain
        return self._default_chain()

    @property
    def collection_mode(self) -> str:
        """返回运维可读的取数方式，避免仅用 requires_render 做错误判断。"""
        if self.source_name.startswith("vision-") or "vision" in self.__class__.__name__.lower():
            return "browser_ocr"
        # 这类旧版抓取器覆写 fetch() 并在浏览器关闭时直接返回空，实际不是可降级的 HTTP 源。
        if self.__class__.fetch is not BaseScraper.fetch and self.requires_render:
            return "browser_required"
        if self._effective_chain == [FetchLevel.RENDER]:
            return "browser_required"
        if FetchLevel.RENDER in self._effective_chain:
            return "http_then_browser"
        return "http"

    @property
    def needs_playwright(self) -> bool:
        return self.collection_mode in {"browser_ocr", "browser_required"}

    @property
    def needs_vision_credentials(self) -> bool:
        return self.collection_mode == "browser_ocr"

    def _default_chain(self) -> list[str]:
        """向后兼容:requires_render=True → [render, html]; False → [html]。"""
        if self.requires_render:
            return [FetchLevel.RENDER, FetchLevel.HTML]
        return [FetchLevel.HTML]

    # ---- 子类实现 ----
    @abc.abstractmethod
    def parse(self, text: str) -> list[RawPrice]:
        """解析已获取的页面文本(HTML 或 JSON 字符串)→ RawPrice 列表。"""
        raise NotImplementedError

    async def fetch_api(self) -> list[RawPrice] | None:
        """Level 1: JSON API 取数。子类覆盖此方法;返回 None 表示该级别不可用。"""
        return None

    async def urls(self) -> list[str]:
        """默认抓 source_url;多货币/多页厂商可覆盖返回多个 URL。"""
        return [self.source_url]

    # ---- 取数 ----
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    async def _http_get(self, url: str) -> str:
        headers = {"User-Agent": settings.user_agent, "Accept-Language": "zh-CN,zh,en"}
        async with httpx.AsyncClient(
            timeout=settings.http_timeout, follow_redirects=True, headers=headers,
            **({"proxy": settings.http_proxy} if settings.http_proxy else {}),
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    async def _render(self, url: str, *, headless: bool = True) -> str:
        """Playwright 渲染，并拦截不会影响价格解析的重资源。

        headless=False 使用有头模式(模拟真实浏览器,反检测能力更强);
        headless=True 为传统无头模式(服务器环境兜底)。
        """
        from playwright.async_api import async_playwright  # 延迟导入

        async with async_playwright() as p:
            launch_options = {
                "headless": headless,
                "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            }
            if settings.http_proxy:
                launch_options["proxy"] = {"server": settings.http_proxy}
            browser = await p.chromium.launch(**launch_options)
            try:
                page = await browser.new_page(user_agent=settings.user_agent)
                await self._prepare_page(page)
                await page.goto(url, wait_until="domcontentloaded", timeout=int(settings.http_timeout * 1000))
                await page.wait_for_timeout(int(settings.render_settle_seconds * 1000))
                return await page.content()
            finally:
                await browser.close()

    @staticmethod
    async def _prepare_page(page) -> None:
        """禁止图片/字体/媒体，显著降低 Chromium 峰值内存。"""
        async def handle(route):
            if route.request.resource_type in {"image", "font", "media"}:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", handle)

    async def _get(self, url: str) -> str:
        if self.requires_render and settings.use_playwright:
            try:
                return await self._render(url)
            except Exception:
                # Playwright 不可用时降级为 HTTP(部分页面 SSR 内容仍可解析)
                return await self._http_get(url)
        return await self._http_get(url)

    # ---- Level 2: HTTP HTML ----
    async def _fetch_html(self) -> list[RawPrice]:
        """HTTP GET 取 HTML/JSON → parse。"""
        urls = await self.urls()
        texts = await asyncio.gather(*(self._http_get(u) for u in urls))
        results: list[RawPrice] = []
        for text in texts:
            results.extend(self.parse(text))
        return results

    # ---- Level 3: Playwright 有头渲染 + 正则解析 ----
    async def _fetch_render(self) -> list[RawPrice]:
        """Playwright 有头渲染 → 取 HTML → parse。
        有头模式更接近真实浏览器,部分 JS 重渲染页面检测 headless 会返回空内容。
        """
        if not settings.use_playwright:
            return []
        urls = await self.urls()
        results: list[RawPrice] = []
        for url in urls:
            try:
                # 服务端默认无头模式，避免 DISPLAY 缺失导致每次先失败一次。
                html = await self._render(url, headless=True)
            except Exception:
                try:
                    # 某些站点对无头模式敏感时，再尝试有头模式。
                    html = await self._render(url, headless=False)
                except Exception:
                    continue
            results.extend(self.parse(html))
        return results

    # ---- 四级降级 fetch ----
    async def fetch(self) -> list[RawPrice]:
        """按降级链依次尝试,直到某级返回非空结果。

        向后兼容:未设置 fetch_chain 的旧子类走原有逻辑。
        """
        chain = self._effective_chain
        name = self.__class__.__name__

        for level in chain:
            try:
                if level == FetchLevel.API:
                    result = await self.fetch_api()
                    if result is None:
                        continue  # 该级别未实现
                elif level == FetchLevel.HTML:
                    result = await self._fetch_html()
                elif level == FetchLevel.RENDER:
                    result = await self._fetch_render()
                elif level == FetchLevel.VISION:
                    # Vision 由 VisionScraper 子类覆盖 fetch()
                    continue
                else:
                    continue

                if result:
                    print(f"  [{name}] {level} 成功: {len(result)} 条")
                    return result
                else:
                    print(f"  [{name}] {level} 返回空,降级…", file=sys.stderr)
            except Exception as exc:
                print(f"  [{name}] {level} 失败: {exc!r},降级…", file=sys.stderr)

        # 所有级别均失败,返回空
        print(f"  [{name}] 所有级别均失败", file=sys.stderr)
        return []
