from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

from playwright.async_api import Browser, Playwright, Route, async_playwright

ALLOWED_PUBLIC_DOMAINS = frozenset(
    {"place.map.kakao.com", "m.place.naver.com", "m.blog.naver.com", "blog.naver.com"}
)
_BLOG_PATH = re.compile(r"^/([^/]+)/(\d+)/?$")


class DomainNotAllowed(Exception):
    """허용 목록에 없는 도메인이다."""


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


class PublicPageReader:
    def __init__(self, allowed_domains: frozenset[str] | None = None) -> None:
        self._allowed = allowed_domains if allowed_domains is not None else ALLOWED_PUBLIC_DOMAINS
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    def _is_allowed(self, url: str) -> bool:
        return _host(url) in self._allowed

    @staticmethod
    def _to_mobile_blog(url: str) -> str:
        parts = urlsplit(url)
        if (parts.hostname or "").lower() == "blog.naver.com" and _BLOG_PATH.match(parts.path):
            return parts._replace(netloc="m.blog.naver.com").geturl()
        return url

    async def _get_browser(self) -> Browser:
        async with self._lock:
            if self._browser is None:
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(headless=True)
            return self._browser

    async def aclose(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None

    async def text(self, url: str, timeout_s: float = 15) -> str | None:
        if not self._is_allowed(url):
            raise DomainNotAllowed(_host(url))
        target = self._to_mobile_blog(url)
        if not self._is_allowed(target):
            raise DomainNotAllowed(_host(target))
        context = None
        try:
            browser = await self._get_browser()
            context = await browser.new_context()

            async def guard(route: Route) -> None:
                if self._is_allowed(route.request.url):
                    await route.continue_()
                else:
                    await route.abort()

            page = await context.new_page()
            await page.route("**/*", guard)
            await page.goto(target, timeout=timeout_s * 1000, wait_until="domcontentloaded")
            if not self._is_allowed(page.url):
                return None
            return await page.evaluate("document.body.innerText")
        except Exception:
            return None
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
