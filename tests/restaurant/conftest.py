"""음식점 파이프라인 테스트용 시나리오: respx로 외부 API를, 가짜 객체로 페이지·리더를 대신한다."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest
from httpx import Response

from dodeed.restaurant.candidates import RestaurantDeps
from dodeed.restaurant.hours import HoursExtract
from dodeed.restaurant.reviews import TasteExtract
from dodeed.restaurant.toilet import ToiletExtract
from dodeed.sources.kakao_local import KakaoLocal
from dodeed.sources.localdata import LocalData
from dodeed.sources.naver_search import NaverSearch
from dodeed.sources.public_page import DomainNotAllowed

KAKAO_KEYWORD = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_REGION = "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json"
NAVER_BLOG = "https://openapi.naver.com/v1/search/blog.json"
NAVER_WEB = "https://openapi.naver.com/v1/search/webkr.json"
LOCALDATA = "https://apis.data.go.kr/1741000/general_restaurants"

ORIGIN = (37.49794, 127.02761)


@dataclass
class PlaceFx:
    id: str
    name: str
    dist_step: int  # 출발지에서 북쪽으로 약 111m 단위
    category: str = "음식점 > 한식 > 국밥"
    road_address: str = "서울 강남구 역삼동 테헤란로 1"
    status: str = "open"  # LOCALDATA: open / closed / unknown
    hours: tuple[bool | None, str | None] | None = (True, "22:00")  # None이면 리더 실패
    blog: list[dict] = field(default_factory=list)  # title, description, postdate, body?
    web: list[dict] = field(default_factory=list)

    @property
    def place_url(self) -> str:
        return f"http://place.map.kakao.com/{self.id}"

    def kakao_doc(self) -> dict:
        return {
            "id": self.id,
            "place_name": self.name,
            "category_name": self.category,
            "road_address_name": self.road_address,
            "address_name": self.road_address,
            "x": str(ORIGIN[1]),
            "y": str(ORIGIN[0] + 0.001 * self.dist_step),
            "phone": "",
            "place_url": self.place_url,
        }


def blog(title: str, description: str = "", postdate: str = "20261001", body: str | None = None):
    return {"title": title, "description": description, "postdate": postdate, "body": body}


def own_money_blogs(n: int = 3) -> list[dict]:
    return [
        blog(
            f"내돈내산 후기 {i}",
            "국물이 진하고 맛있어요",
            f"202610{10 + i:02d}",
            body=f"직접 결제하고 먹은 후기 {i}. 국물이 진해요.",
        )
        for i in range(n)
    ]


class FakePageReader:
    def __init__(self, scenario: Scenario) -> None:
        self.s = scenario
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self.slow_marker: str | None = None  # URL에 이 글자가 있으면 오래 걸린다

    async def text(self, url: str, timeout_s: float = 15) -> str | None:
        self.calls.append(url)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.005)
            if self.slow_marker and self.slow_marker in url:
                await asyncio.sleep(30)
            if "example.com" in url:
                raise DomainNotAllowed("example.com")
            for p in self.s.places:
                if url == p.place_url:
                    return f"[장소페이지] {p.id}"
                for b in p.blog:
                    if url == b.get("link") and b.get("body"):
                        return b["body"]
            return None
        finally:
            self.active -= 1


class FakeReader:
    """스키마별로 정해진 값을 돌려주는 격리 리더 대역."""

    def __init__(self, scenario: Scenario) -> None:
        self.s = scenario
        self.calls: list[tuple[type, str, str]] = []

    async def extract(self, raw, schema, instruction):
        self.calls.append((schema, raw, instruction))
        if schema is HoursExtract:
            m = re.search(r"\[장소페이지\] (\S+)", raw)
            place = next((p for p in self.s.places if m and p.id == m.group(1)), None)
            if place is None or place.hours is None:
                return None
            return HoursExtract(open_now=place.hours[0], closes_at=place.hours[1])
        if schema is ToiletExtract:
            if "화장실 매장 안" in raw:
                return ToiletExtract(gender_separated=True, location="inside")
            if "화장실 건물 공용" in raw:
                return ToiletExtract(gender_separated=False, location="building_shared")
            return ToiletExtract(gender_separated=None, location="unknown")
        if schema is TasteExtract:
            return TasteExtract(summary="국물이 진하고 깔끔해요")
        raise AssertionError(f"예상하지 못한 스키마: {schema}")


class Scenario:
    def __init__(self, respx_mock, settings, http: httpx.AsyncClient) -> None:
        self.respx = respx_mock
        self.places: list[PlaceFx] = []
        self.kakao_down = False
        self.blog_down = False
        self.geocode_result: tuple[float, float, str] | None = (*ORIGIN, "강남역 2호선")
        self.keyword_queries: list[str] = []
        self.blog_queries: list[str] = []
        self.page_reader = FakePageReader(self)
        self.reader = FakeReader(self)
        self.deps = RestaurantDeps(
            kakao=KakaoLocal(http, settings),
            naver=NaverSearch(http, settings),
            localdata=LocalData(http, settings),
            reader=self.reader,
            page_reader=self.page_reader,
        )
        respx_mock.get(KAKAO_KEYWORD).mock(side_effect=self._kakao_keyword)
        respx_mock.get(KAKAO_REGION).respond(
            json={"documents": [{"region_type": "H", "region_3depth_name": "역삼1동"}]}
        )
        respx_mock.get(NAVER_BLOG).mock(side_effect=self._blog)
        respx_mock.get(NAVER_WEB).mock(side_effect=self._web)
        respx_mock.get(LOCALDATA).mock(side_effect=self._localdata)

    def add(self, *places: PlaceFx) -> Scenario:
        self.places.extend(places)
        return self

    def _kakao_keyword(self, request: httpx.Request) -> Response:
        if self.kakao_down:
            return Response(503)
        params = request.url.params
        if "category_group_code" not in params:  # geocode_area
            if self.geocode_result is None:
                return Response(200, json={"documents": []})
            lat, lng, name = self.geocode_result
            return Response(
                200, json={"documents": [{"y": str(lat), "x": str(lng), "place_name": name}]}
            )
        self.keyword_queries.append(params["query"])
        size = int(params.get("size", 15))
        return Response(200, json={"documents": [p.kakao_doc() for p in self.places][:size]})

    def _place_for(self, query: str) -> PlaceFx | None:
        for p in sorted(self.places, key=lambda p: -len(p.name)):
            if query.startswith(p.name + " "):
                return p
        return None

    def _blog(self, request: httpx.Request) -> Response:
        if self.blog_down:
            return Response(503)
        query = request.url.params["query"]
        self.blog_queries.append(query)
        p = self._place_for(query)
        items = []
        if p is not None:
            for i, b in enumerate(p.blog):
                b.setdefault("link", f"https://blog.naver.com/u{p.id}/22300{i:05d}")
                items.append(
                    {
                        "title": b["title"],
                        "link": b["link"],
                        "description": b["description"],
                        "postdate": b["postdate"],
                    }
                )
        return Response(200, json={"items": items})

    def _web(self, request: httpx.Request) -> Response:
        p = self._place_for(request.url.params["query"])
        items = [
            {"title": w["title"], "link": w["link"], "description": w["description"]}
            for w in (p.web if p else [])
        ]
        return Response(200, json={"items": items})

    def _localdata(self, request: httpx.Request) -> Response:
        name = request.url.params["cond[BPLC_NM::LIKE]"]
        rows = []
        for p in self.places:
            if p.name == name and p.status != "unknown":
                rows.append(
                    {
                        "BPLC_NM": p.name,
                        "ROAD_NM_ADDR": p.road_address,
                        "SALS_STTS_CD": "03" if p.status == "closed" else "01",
                        "SALS_STTS_NM": "폐업" if p.status == "closed" else "영업/정상",
                    }
                )
        return Response(200, json={"response": {"body": {"items": {"item": rows}}}})


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
def scenario(respx_mock, settings, http) -> Scenario:
    return Scenario(respx_mock, settings, http)


@pytest.fixture
def kst_now() -> datetime:
    # 2026-11-01 (일) 21:00 KST
    return datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
