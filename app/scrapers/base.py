"""抓取器框架:BaseScraper 抽象类,提供 HTTP 与 Playwright 两种取数方式。"""
from __future__ import annotations

import abc
import asyncio

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.models.pricing import RawPrice


class BaseScraper(abc.ABC):
    """所有抓取器的基类。

    子类需声明 provider/source_url,并实现 parse(html_or_json) 返回 RawPrice 列表。
    默认走 HTTP GET;若页面为 JS 重渲染,设 requires_render=True 走 Playwright。
    parse 与网络分离,便于用离线 fixture 做单测。
    """

    provider: str
    channel: str = "official"
    source_url: str
    requires_render: bool = False

    @property
    def source_name(self) -> str:
        """数据源标识,用于多源交叉验证。默认取类名去掉 Scraper 后缀。"""
        return self.__class__.__name__.replace("Scraper", "").lower()

    # ---- 子类实现 ----
    @abc.abstractmethod
    def parse(self, text: str) -> list[RawPrice]:
        """解析已获取的页面文本(HTML 或 JSON 字符串)→ RawPrice 列表。"""
        raise NotImplementedError

    async def urls(self) -> list[str]:
        """默认抓 source_url;多货币/多页厂商可覆盖返回多个 URL。"""
        return [self.source_url]

    # ---- 取数 ----
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    async def _http_get(self, url: str) -> str:
        headers = {"User-Agent": settings.user_agent, "Accept-Language": "zh-CN,zh,en"}
        async with httpx.AsyncClient(
            timeout=settings.http_timeout, follow_redirects=True, headers=headers
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    async def _render(self, url: str) -> str:
        """Playwright 渲染。未安装或未启用时抛错,由 fetch 决定降级。"""
        from playwright.async_api import async_playwright  # 延迟导入

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(user_agent=settings.user_agent)
                await page.goto(url, wait_until="networkidle", timeout=int(settings.http_timeout * 1000))
                return await page.content()
            finally:
                await browser.close()

    async def _get(self, url: str) -> str:
        if self.requires_render and settings.use_playwright:
            try:
                return await self._render(url)
            except Exception:
                # Playwright 不可用时降级为 HTTP(部分页面 SSR 内容仍可解析)
                return await self._http_get(url)
        return await self._http_get(url)

    async def fetch(self) -> list[RawPrice]:
        """抓取全部 URL 并解析,合并结果。"""
        urls = await self.urls()
        texts = await asyncio.gather(*(self._get(u) for u in urls))
        results: list[RawPrice] = []
        for text in texts:
            results.extend(self.parse(text))
        return results
