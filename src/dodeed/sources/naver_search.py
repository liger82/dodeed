from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from dodeed.config import Settings
from dodeed.sources import SourceUnavailable, get_json

BLOG_URL = "https://openapi.naver.com/v1/search/blog.json"
WEB_URL = "https://openapi.naver.com/v1/search/webkr.json"
_B_TAG = re.compile(r"</?b>", re.IGNORECASE)


@dataclass
class SearchItem:
    title: str
    link: str
    description: str
    postdate: date | None


def _clean(s: str) -> str:
    return html.unescape(_B_TAG.sub("", s))


def _postdate(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


class NaverSearch:
    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http
        self._headers = {
            "X-Naver-Client-Id": settings.naver_client_id,
            "X-Naver-Client-Secret": settings.naver_client_secret,
        }

    async def _search(self, url: str, query: str, display: int) -> list[SearchItem]:
        data = await get_json(
            self._http, url, params={"query": query, "display": display}, headers=self._headers
        )
        try:
            return [
                SearchItem(
                    title=_clean(i["title"]),
                    link=i["link"],
                    description=_clean(i.get("description", "")),
                    postdate=_postdate(i.get("postdate")),
                )
                for i in data.get("items", [])
            ]
        except (KeyError, TypeError, AttributeError) as exc:
            raise SourceUnavailable("예상하지 못한 응답 형식") from exc

    async def search_blog(self, query: str, display: int = 20) -> list[SearchItem]:
        return await self._search(BLOG_URL, query, display)

    async def search_web(self, query: str, display: int = 10) -> list[SearchItem]:
        return await self._search(WEB_URL, query, display)
