import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from dodeed.sources import SourceUnavailable
from dodeed.sources.geo import walking_minutes
from dodeed.sources.kakao_local import KakaoLocal
from dodeed.sources.localdata import LocalData
from dodeed.sources.naver_search import NaverSearch

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def fx(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as c:
        yield c


def test_walking_minutes_gangnam_sample():
    assert walking_minutes(37.4979, 127.0276, 37.5009, 127.0266) == 7


async def test_search_keyword_parses_places(respx_mock, http, settings):
    route = respx_mock.get("https://dapi.kakao.com/v2/local/search/keyword.json").respond(
        json=fx("kakao/keyword.json")
    )
    places = await KakaoLocal(http, settings).search_keyword("강남역 맛집", 127.0, 37.5)
    assert places[0].name == "을지식당" and places[0].lat == 37.4979 and places[0].lng == 127.0276
    assert places[0].road_address == "서울 강남구 테헤란로 1"
    assert places[1].road_address == "서울 강남구 역삼동 2-2"
    req = route.calls.last.request
    assert req.headers["Authorization"] == "KakaoAK test-kakao-key"
    assert req.url.params["category_group_code"] == "FD6"
    assert req.url.params["sort"] == "distance"


async def test_search_keyword_without_coords_uses_accuracy(respx_mock, http, settings):
    route = respx_mock.get("https://dapi.kakao.com/v2/local/search/keyword.json").respond(
        json=fx("kakao/keyword.json")
    )
    await KakaoLocal(http, settings).search_keyword("x", None, None)
    assert route.calls.last.request.url.params["sort"] == "accuracy"
    assert "x" not in route.calls.last.request.url.params


async def test_geocode_area(respx_mock, http, settings):
    route = respx_mock.get("https://dapi.kakao.com/v2/local/search/keyword.json").respond(
        json=fx("kakao/geocode.json")
    )
    assert await KakaoLocal(http, settings).geocode_area("강남역") == (
        37.49794,
        127.02761,
        "강남역 2호선",
    )
    assert "category_group_code" not in route.calls.last.request.url.params


async def test_geocode_area_none(respx_mock, http, settings):
    respx_mock.get("https://dapi.kakao.com/v2/local/search/keyword.json").respond(
        json={"documents": []}
    )
    assert await KakaoLocal(http, settings).geocode_area("없음") is None


async def test_region_name_prefers_h(respx_mock, http, settings):
    respx_mock.get("https://dapi.kakao.com/v2/local/geo/coord2regioncode.json").respond(
        json=fx("kakao/region.json")
    )
    assert await KakaoLocal(http, settings).region_name(37.5, 127.03) == "역삼1동"


async def test_kakao_429_retries_once_then_raises(respx_mock, http, settings):
    route = respx_mock.get("https://dapi.kakao.com/v2/local/search/keyword.json").respond(429)
    with pytest.raises(SourceUnavailable):
        await KakaoLocal(http, settings).search_keyword("x", None, None)
    assert route.call_count == 2


async def test_kakao_retry_succeeds(respx_mock, http, settings):
    respx_mock.get("https://dapi.kakao.com/v2/local/search/keyword.json").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=fx("kakao/keyword.json"))]
    )
    assert len(await KakaoLocal(http, settings).search_keyword("x", None, None)) == 2


async def test_kakao_400_no_retry(respx_mock, http, settings):
    route = respx_mock.get("https://dapi.kakao.com/v2/local/search/keyword.json").respond(401)
    with pytest.raises(SourceUnavailable):
        await KakaoLocal(http, settings).search_keyword("x", None, None)
    assert route.call_count == 1


async def test_kakao_timeout_maps(respx_mock, http, settings):
    respx_mock.get("https://dapi.kakao.com/v2/local/search/keyword.json").mock(
        side_effect=httpx.ConnectTimeout("t")
    )
    with pytest.raises(SourceUnavailable):
        await KakaoLocal(http, settings).search_keyword("x", None, None)


async def test_naver_strips_b_tags(respx_mock, http, settings):
    route = respx_mock.get("https://openapi.naver.com/v1/search/blog.json").respond(
        json=fx("naver/blog.json")
    )
    items = await NaverSearch(http, settings).search_blog("을지식당")
    assert items[0].title == "을지식당 방문 후기 & 웨이팅"
    assert items[0].description == '국물이 "진한" 국밥 맛집'
    assert items[0].postdate == date(2026, 10, 20)
    h = route.calls.last.request.headers
    assert h["X-Naver-Client-Id"] == "test-naver-id"
    assert h["X-Naver-Client-Secret"] == "test-naver-secret"


async def test_naver_web_no_postdate(respx_mock, http, settings):
    respx_mock.get("https://openapi.naver.com/v1/search/webkr.json").respond(
        json=fx("naver/web.json")
    )
    items = await NaverSearch(http, settings).search_web("x")
    assert items[0].postdate is None and items[0].description == "영업시간 안내 <안내>"


URL = "https://apis.data.go.kr/1741000/general_restaurants/info"


async def test_localdata_open_and_closed(respx_mock, http, settings):
    respx_mock.get(URL).respond(json=fx("localdata/general_restaurants.json"))
    ld = LocalData(http, settings)
    assert await ld.business_status("을지 식당", "서울특별시 강남구 테헤란로 1") == "open"
    assert await ld.business_status("폐업집", "서울특별시 강남구 테헤란로 9") == "closed"


async def test_localdata_unknown_when_no_match(respx_mock, http, settings):
    respx_mock.get(URL).respond(json=fx("localdata/general_restaurants.json"))
    assert await LocalData(http, settings).business_status("을지식당", "다른 주소") == "unknown"


async def test_localdata_unknown_on_failure_and_bad_shape(respx_mock, http, settings):
    route = respx_mock.get(URL).respond(500)
    assert await LocalData(http, settings).business_status("a", "b") == "unknown"
    route.respond(json={"weird": 1})
    assert await LocalData(http, settings).business_status("a", "b") == "unknown"
