import asyncio
import time

import pytest

from dodeed.restaurant import candidates as candidates_mod
from dodeed.restaurant.candidates import (
    BLOG_BODY_TOP,
    PAGE_CONCURRENCY,
    CandidateList,
    NeedLocation,
    SearchRequest,
    find_candidates,
)
from dodeed.restaurant.hours import HoursExtract
from dodeed.restaurant.reviews import TasteExtract
from dodeed.restaurant.situations import Situation
from dodeed.restaurant.toilet import ToiletExtract
from dodeed.security.egress import EgressBlocked

from .conftest import ORIGIN, PlaceFx, blog, own_money_blogs

HERE = {"lat": ORIGIN[0], "lng": ORIGIN[1], "at": "2026-11-01T11:59:00+00:00"}


def names(cl: CandidateList) -> list[str]:
    return [c.name for c in cl.candidates]


async def test_here_without_location_returns_need_location(scenario, kst_now):
    req = SearchRequest(use_current_location=True)
    assert await find_candidates(scenario.deps, req, None, kst_now) == NeedLocation()
    assert scenario.keyword_queries == []


async def test_no_origin_at_all_returns_need_location(scenario, kst_now):
    assert await find_candidates(scenario.deps, SearchRequest(), None, kst_now) == NeedLocation()


async def test_current_location_used_and_region_named(scenario, kst_now):
    scenario.add(PlaceFx("1", "가집", 1))
    cl = await find_candidates(
        scenario.deps, SearchRequest(use_current_location=True), HERE, kst_now
    )
    assert isinstance(cl, CandidateList)
    assert cl.origin_area == "역삼1동"
    assert names(cl) == ["가집"]


async def test_state_area_name_preferred_over_region_lookup(scenario, kst_now):
    scenario.add(PlaceFx("1", "가집", 1))
    loc = {**HERE, "area_name": "회사 앞"}
    cl = await find_candidates(scenario.deps, SearchRequest(), loc, kst_now)
    assert cl.origin_area == "회사 앞"


async def test_area_query_geocoded(scenario, kst_now):
    scenario.add(PlaceFx("1", "가집", 1))
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert cl.origin_area == "강남역 2호선"
    assert cl.candidates[0].walk_min == 3


async def test_area_not_found_notice(scenario, kst_now):
    scenario.geocode_result = None
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="어딘가"), None, kst_now)
    assert cl.candidates == []
    assert cl.notice == "말씀하신 곳을 찾지 못했어요. 지명을 조금 더 구체적으로 알려주시겠어요?"


async def test_closed_and_not_open_excluded(scenario, kst_now):
    scenario.add(
        PlaceFx("1", "폐업집", 1, status="closed"),
        PlaceFx("2", "닫은집", 2, hours=(False, None)),
        PlaceFx("3", "연집", 3),
        PlaceFx("4", "모름집", 4, status="unknown", hours=None),
    )
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert names(cl) == ["연집", "모름집"]
    by_name = {c.name: c for c in cl.candidates}
    assert by_name["연집"].closed_permanently is False
    assert by_name["연집"].open_status.open_now is True
    assert by_name["연집"].open_status.closes_at == "22:00"
    assert by_name["모름집"].closed_permanently is None
    assert by_name["모름집"].open_status.open_now is None


async def test_toilet_inside_orders_unknown_before_shared(scenario, kst_now):
    scenario.add(
        PlaceFx("1", "공용집", 1, blog=[blog("후기", "화장실 건물 공용이에요")]),
        PlaceFx("2", "모름집", 2),
        PlaceFx("3", "안쪽집", 3, blog=[blog("후기", "화장실 매장 안에 있어요")]),
    )
    req = SearchRequest(area_query="강남역", toilet_inside=True)
    cl = await find_candidates(scenario.deps, req, None, kst_now)
    assert names(cl) == ["안쪽집", "모름집", "공용집"]
    assert [c.toilet.location for c in cl.candidates] == ["inside", "unknown", "building_shared"]


async def test_without_toilet_inside_sorted_by_walk(scenario, kst_now):
    scenario.add(
        PlaceFx("1", "먼집", 5, blog=[blog("후기", "화장실 매장 안에 있어요")]),
        PlaceFx("2", "가까운집", 1),
    )
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert names(cl) == ["가까운집", "먼집"]


async def test_web_search_feeds_toilet(scenario, kst_now):
    scenario.add(
        PlaceFx(
            "1",
            "가집",
            1,
            web=[
                {
                    "title": "가집 화장실",
                    "link": "https://example.com/q",
                    "description": "화장실 매장 안 남녀 분리",
                }
            ],
        )
    )
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    t = cl.candidates[0].toilet
    assert t.location == "inside" and t.source_link == "https://example.com/q"
    assert t.source_kind == "web"


async def test_partial_failure_marks_unknown(scenario, kst_now):
    scenario.blog_down = True
    scenario.add(PlaceFx("1", "가집", 1, blog=own_money_blogs(5), hours=None))
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert names(cl) == ["가집"]
    c = cl.candidates[0]
    assert c.taste.status == "insufficient"
    assert c.toilet.location == "unknown"
    assert c.open_status.open_now is None
    assert cl.notice is None


async def test_taste_from_own_money_blogs(scenario, kst_now):
    scenario.add(PlaceFx("1", "가집", 1, blog=own_money_blogs(4)))
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    taste = cl.candidates[0].taste
    assert taste.status == "ok" and taste.evidence_count == 4
    assert taste.summary == "국물이 진하고 깔끔해요"


async def test_blog_query_uses_district_and_top5_bodies(scenario, kst_now):
    posts = [blog(f"글{i}", "설명", f"202610{i + 1:02d}", body=f"본문{i}") for i in range(12)]
    scenario.add(PlaceFx("1", "가집", 1, road_address="서울 강남구 역삼동 테헤란로 1", blog=posts))
    await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert scenario.blog_queries == ["가집 강남구 역삼동"]
    body_reads = [u for u in scenario.page_reader.calls if "blog.naver.com" in u]
    assert BLOG_BODY_TOP == 5
    assert len(body_reads) == 5


async def test_blog_query_falls_back_to_first_two_tokens(scenario, kst_now):
    scenario.add(PlaceFx("1", "가집", 1, road_address="경기 성남시 판교역로 1"))
    await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert scenario.blog_queries == ["가집 경기 성남시"]


async def test_page_reads_limited_to_6_concurrent(scenario, kst_now):
    for i in range(8):
        posts = [blog(f"글{j}", "설명", body=f"본문{j}") for j in range(4)]
        scenario.add(PlaceFx(str(i), f"집{i}", i + 1, blog=posts))
    await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert len(scenario.page_reader.calls) == 8 * 5
    assert PAGE_CONCURRENCY == 6
    assert 3 < scenario.page_reader.max_active <= PAGE_CONCURRENCY


async def test_page_slots_shared_across_searches(scenario, kst_now):
    for i in range(8):
        posts = [blog(f"글{j}", "설명", body=f"본문{j}") for j in range(4)]
        scenario.add(PlaceFx(str(i), f"집{i}", i + 1, blog=posts))
    req = SearchRequest(area_query="강남역")
    await asyncio.gather(
        find_candidates(scenario.deps, req, None, kst_now),
        find_candidates(scenario.deps, req, None, kst_now),
    )
    assert len(scenario.page_reader.calls) == 2 * 8 * 5
    assert scenario.page_reader.max_active <= PAGE_CONCURRENCY


async def test_hours_instruction_has_kst_now(scenario, kst_now):
    scenario.add(PlaceFx("1", "가집", 1))
    await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    hours_calls = [c for c in scenario.reader.calls if c[0] is HoursExtract]
    assert len(hours_calls) == 1
    assert "현재 시각: 2026-11-01 (일) 21:00 KST" in hours_calls[0][2]


async def test_only_top_8_enriched(scenario, kst_now):
    scenario.add(*[PlaceFx(str(i), f"집{i}", i + 1) for i in range(12)])
    await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert len(scenario.blog_queries) == 8


async def test_no_results_suggests_relaxing(scenario, kst_now):
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert cl.candidates == []
    assert (
        cl.notice
        == "조건에 맞는 곳을 찾지 못했어요. 반경을 넓히거나 조건을 하나 빼서 다시 찾아볼까요?"
    )


async def test_all_filtered_out_suggests_relaxing(scenario, kst_now):
    scenario.add(PlaceFx("1", "폐업집", 1, status="closed"))
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert cl.candidates == [] and cl.notice.startswith("조건에 맞는 곳을 찾지 못했어요.")


async def test_kakao_down_returns_notice(scenario, kst_now):
    scenario.kakao_down = True
    cl = await find_candidates(scenario.deps, SearchRequest(), HERE, kst_now)
    assert cl.candidates == []
    assert (
        cl.notice == "장소 검색 서비스에 일시적으로 연결할 수 없어요. 잠시 후 다시 시도해 주세요."
    )


async def test_at_most_5_candidates(scenario, kst_now):
    scenario.add(*[PlaceFx(str(i), f"집{i}", i + 1) for i in range(7)])
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert names(cl) == [f"집{i}" for i in range(5)]


async def test_default_time_reported(scenario, kst_now):
    scenario.add(PlaceFx("1", "가집", 1))
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert cl.defaults_used == ["시간: 지금"]
    req = SearchRequest(area_query="강남역", time_text="오늘 7시")
    cl2 = await find_candidates(scenario.deps, req, None, kst_now)
    assert cl2.defaults_used == []


async def test_kakao_query_and_retry_when_few(scenario, kst_now):
    scenario.add(PlaceFx("1", "가집", 1))
    req = SearchRequest(area_query="강남역", situation=Situation.SOLO, cuisine="국밥")
    await find_candidates(scenario.deps, req, None, kst_now)
    assert scenario.keyword_queries == ["국밥 혼밥", "국밥"]


async def test_kakao_default_query(scenario, kst_now):
    scenario.add(*[PlaceFx(str(i), f"집{i}", i + 1) for i in range(3)])
    await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert scenario.keyword_queries == ["맛집"]


async def test_retry_dedupes_places(scenario, kst_now):
    scenario.add(PlaceFx("1", "가집", 1), PlaceFx("2", "나집", 2))
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert scenario.keyword_queries == ["맛집", "음식점"]
    assert names(cl) == ["가집", "나집"]


async def test_egress_blocked_propagates(scenario, kst_now):
    scenario.add(PlaceFx("1", "가집", 1))

    async def boom(raw, schema, instruction):
        raise EgressBlocked("secret")

    scenario.reader.extract = boom
    with pytest.raises(EgressBlocked):
        await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)


def test_reader_schemas_use_simple_types():
    for schema in (HoursExtract, ToiletExtract, TasteExtract):
        js = str(schema.model_json_schema())
        assert "date" not in js and "enum" not in js and "const" not in js


SPONSORED_BODY = "본 포스팅은 소정의 원고료를 받아 작성되었습니다. 국물이 진해요."


async def test_snippet_own_money_with_sponsored_body_never_reaches_taste(scenario, kst_now):
    """제목은 내돈내산인데 대가성 표시가 본문에만 있는 글은 본문을 못 읽었으면 맛 평가에 안 쓴다."""
    # 가: 본문 읽기가 모두 실패 (페이지 리더가 None)
    failed = [blog(f"내돈내산 후기 {i}", "맛있어요", f"202610{i + 1:02d}") for i in range(4)]
    # 나: 상위 5건은 본문을 읽어 대가성으로 분류되고, 6번째부터는 본문을 읽지 않는다
    unread = [
        blog(f"내돈내산 후기 {i}", "맛있어요", f"202610{i + 1:02d}", body=SPONSORED_BODY)
        for i in range(9)
    ]
    scenario.add(PlaceFx("1", "가집", 1, blog=failed), PlaceFx("2", "나집", 2, blog=unread))
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert [c.taste.status for c in cl.candidates] == ["insufficient", "insufficient"]
    assert [c for c in scenario.reader.calls if c[0] is TasteExtract] == []
    assert len([u for u in scenario.page_reader.calls if "/u2/" in u]) == 5


async def test_body_read_own_money_reaches_taste_only_with_bodies(scenario, kst_now):
    posts = own_money_blogs(3) + [blog("내돈내산 SNIPPET-ONLY", "본문 없음", "20261030")]
    scenario.add(PlaceFx("1", "가집", 1, blog=posts))
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert cl.candidates[0].taste.evidence_count == 3
    taste_calls = [c for c in scenario.reader.calls if c[0] is TasteExtract]
    assert len(taste_calls) == 1 and "SNIPPET-ONLY" not in taste_calls[0][1]


async def test_enrich_deadline_returns_partial_results(scenario, kst_now, monkeypatch):
    monkeypatch.setattr(candidates_mod, "ENRICH_DEADLINE_S", 0.3)
    scenario.page_reader.slow_marker = "blog.naver.com"  # 블로그 본문 읽기가 30초 걸린다
    scenario.add(PlaceFx("1", "가집", 1, blog=own_money_blogs(4)))
    started = time.monotonic()
    cl = await find_candidates(scenario.deps, SearchRequest(area_query="강남역"), None, kst_now)
    assert time.monotonic() - started < 5
    assert [c.name for c in cl.candidates] == ["가집"]
    c = cl.candidates[0]
    assert c.open_status.open_now is True  # 끝난 항목은 남는다
    assert c.taste.status == "insufficient" and c.toilet.location == "unknown"
