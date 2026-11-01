import pytest

from dodeed.restaurant.candidates import SearchRequest, find_candidates
from dodeed.restaurant.situations import SITUATIONS, Situation, format_won

from .conftest import PlaceFx, blog


@pytest.fixture
def deps_with_fixtures(scenario):
    scenario.add(
        PlaceFx(
            "1",
            "한그릇",
            1,
            category="음식점 > 한식 > 국밥",
            blog=[blog("혼밥하기 좋은 곳", "1인석이 많고 조용한 분위기, 가성비 최고")],
        ),
        PlaceFx(
            "2",
            "바스트로",
            2,
            category="음식점 > 양식",
            blog=[blog("데이트 코스", "분위기 좋은 레스토랑. 바 좌석도 있어요")],
        ),
        PlaceFx("3", "그냥집", 3, category="음식점 > 한식", blog=[blog("후기", "평범해요")]),
    )
    return scenario


@pytest.mark.parametrize(
    "sit,price,kw",
    [
        (Situation.SOLO, 15000, "1인석"),
        (Situation.DATE, None, "분위기"),
        (Situation.VALUE, 12000, "가성비"),
    ],
)
async def test_situation_maps_to_conditions_and_reason(sit, price, kw, deps_with_fixtures, kst_now):
    profile = SITUATIONS[sit]
    assert profile.price_max == price and kw in profile.keywords
    req = SearchRequest(situation=sit, area_query="강남역")
    cl = await find_candidates(deps_with_fixtures.deps, req, None, kst_now)
    assert cl.candidates
    for c in cl.candidates:
        assert c.reason.startswith(profile.label)
    assert any(kw in c.reason for c in cl.candidates)
    if price is not None:
        assert all(f"{format_won(price)} 이하" in c.reason for c in cl.candidates)
    assert deps_with_fixtures.keyword_queries[0] == profile.keywords[0]


async def test_keyword_match_sorted_first(deps_with_fixtures, kst_now):
    req = SearchRequest(situation=Situation.DATE, area_query="강남역")
    cl = await find_candidates(deps_with_fixtures.deps, req, None, kst_now)
    # 한그릇(조용한·분위기), 바스트로(데이트·분위기)가 키워드가 맞아 앞에, 그냥집은 뒤에
    assert [c.name for c in cl.candidates] == ["한그릇", "바스트로", "그냥집"]
    by = {c.name: c.reason for c in cl.candidates}
    assert by["한그릇"] == "데이트: 분위기, 조용한"
    assert by["바스트로"] == "데이트: 분위기, 데이트"
    assert by["그냥집"] == "데이트"


async def test_solo_reason_example(deps_with_fixtures, kst_now):
    req = SearchRequest(situation=Situation.SOLO, area_query="강남역")
    cl = await find_candidates(deps_with_fixtures.deps, req, None, kst_now)
    by = {c.name: c.reason for c in cl.candidates}
    assert by["한그릇"] == "혼밥: 혼밥, 1인석, 1만 5천 원 이하"
    assert by["그냥집"] == "혼밥: 1만 5천 원 이하"


async def test_user_price_overrides_situation_price(deps_with_fixtures, kst_now):
    req = SearchRequest(situation=Situation.VALUE, area_query="강남역", price_max=10000)
    cl = await find_candidates(deps_with_fixtures.deps, req, None, kst_now)
    assert all("1만 원 이하" in c.reason for c in cl.candidates)


async def test_none_reason_lists_conditions(deps_with_fixtures, kst_now):
    req = SearchRequest(area_query="강남역", cuisine="국밥", price_max=15000)
    cl = await find_candidates(deps_with_fixtures.deps, req, None, kst_now)
    assert {c.reason for c in cl.candidates} == {"국밥, 1만 5천 원 이하"}
    assert deps_with_fixtures.keyword_queries[0] == "국밥"


async def test_none_without_conditions_is_nearest(deps_with_fixtures, kst_now):
    cl = await find_candidates(
        deps_with_fixtures.deps, SearchRequest(area_query="강남역"), None, kst_now
    )
    assert [c.reason for c in cl.candidates] == ["가까운 순"] * 3
    assert [c.name for c in cl.candidates] == ["한그릇", "바스트로", "그냥집"]


@pytest.mark.parametrize(
    "n,text",
    [
        (15000, "1만 5천 원"),
        (12000, "1만 2천 원"),
        (10000, "1만 원"),
        (8000, "8천 원"),
        (25000, "2만 5천 원"),
    ],
)
def test_format_won(n, text):
    assert format_won(n) == text
