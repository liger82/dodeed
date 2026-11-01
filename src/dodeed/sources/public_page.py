from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlsplit

from playwright.async_api import Browser, Playwright, Route, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

ALLOWED_PUBLIC_DOMAINS = frozenset(
    {"place.map.kakao.com", "m.place.naver.com", "m.blog.naver.com", "blog.naver.com"}
)
# 탐색(문서 요청)이 아닌 하위 리소스(스크립트, 이미지, API)만 이 접미사의 호스트를 허용한다.
# 카카오 장소 페이지와 네이버 블로그는 이 CDN·API에서 본문을 그린다.
SUBRESOURCE_ALLOWED_SUFFIXES = (
    ".kakaocdn.net",
    ".daumcdn.net",
    ".kakao.com",
    ".pstatic.net",
    ".naver.net",
    ".naver.com",
)
NETWORK_IDLE_TIMEOUT_MS = 5000
_BLOG_PATH = re.compile(r"^/([^/]+)/(\d+)/?$")


class DomainNotAllowed(Exception):
    """허용 목록에 없는 도메인이다."""


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


class PublicPageReader:
    def __init__(
        self,
        allowed_domains: frozenset[str] | None = None,
        subresource_suffixes: tuple[str, ...] | None = None,
    ) -> None:
        self._allowed = allowed_domains if allowed_domains is not None else ALLOWED_PUBLIC_DOMAINS
        self._sub_suffixes = (
            subresource_suffixes
            if subresource_suffixes is not None
            else SUBRESOURCE_ALLOWED_SUFFIXES
        )
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    def _is_allowed(self, url: str) -> bool:
        return _host(url) in self._allowed

    def _subresource_allowed(self, url: str) -> bool:
        host = _host(url)
        if not host:
            return False
        if host in self._allowed:
            return True
        for suffix in self._sub_suffixes:
            if suffix.startswith("."):
                if host.endswith(suffix) or host == suffix[1:]:
                    return True
            elif host == suffix:
                return True
        return False

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
            # 서비스 워커는 route 가로채기를 우회할 수 있으므로 막는다
            context = await browser.new_context(service_workers="block")

            async def guard(route: Route) -> None:
                request = route.request
                if request.is_navigation_request():
                    allowed = self._is_allowed(request.url)  # 문서 탐색은 허용 목록 그대로
                else:
                    allowed = self._subresource_allowed(request.url)
                if allowed:
                    await route.continue_()
                else:
                    await route.abort()

            page = await context.new_page()
            await page.route("**/*", guard)
            await page.goto(target, timeout=timeout_s * 1000, wait_until="domcontentloaded")
            if not self._is_allowed(page.url):
                return None
            # SPA는 본문을 나중에 그리므로 네트워크가 잠잠해질 때까지 잠깐 기다린다
            try:
                await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                pass
            if not self._is_allowed(page.url):
                return None
            return await page.evaluate("document.body.innerText")
        except Exception as exc:
            # 예외 메시지에는 URL 등이 들어 있을 수 있으므로 종류 이름만 남긴다
            logger.warning("공개 페이지 읽기 실패: %s", type(exc).__name__)
            return None
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
