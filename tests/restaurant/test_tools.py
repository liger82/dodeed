from datetime import UTC, datetime

from dodeed.policy.tools import Grade, ToolContext, ToolRegistry
from dodeed.restaurant.candidates import CandidateList, NeedLocation, SearchRequest
from dodeed.restaurant.tools import SEARCH_RESTAURANTS, handle_search, register
from dodeed.sessions.service import get_or_create, get_state, set_state

from .conftest import ORIGIN, PlaceFx


def test_spec():
    assert SEARCH_RESTAURANTS.name == "search_restaurants"
    assert SEARCH_RESTAURANTS.service == "dodeed"
    assert SEARCH_RESTAURANTS.grade == Grade.READ
    assert SEARCH_RESTAURANTS.input_model is SearchRequest
    assert SEARCH_RESTAURANTS.description


async def test_handle_search_saves_candidates_merged(scenario, db_session, signed_up_user_id):
    scenario.add(PlaceFx("1", "가집", 1))
    conv = await get_or_create(db_session, signed_up_user_id, "web")
    loc = {"lat": ORIGIN[0], "lng": ORIGIN[1], "at": "2026-11-01T11:59:00+00:00"}
    await set_state(db_session, conv.id, {"location": loc, "other": 1})
    ctx = ToolContext(user_id=signed_up_user_id, db=db_session, session_id=conv.id)
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    result = await handle_search(
        ctx, SearchRequest(use_current_location=True), deps=scenario.deps, now=now
    )
    assert isinstance(result, CandidateList) and result.candidates[0].name == "가집"
    state = await get_state(db_session, conv.id)
    assert state["location"] == loc and state["other"] == 1
    assert CandidateList.model_validate(state["candidates"]) == result


async def test_handle_search_need_location_not_saved(scenario, db_session, signed_up_user_id):
    conv = await get_or_create(db_session, signed_up_user_id, "web")
    ctx = ToolContext(user_id=signed_up_user_id, db=db_session, session_id=conv.id)
    result = await handle_search(ctx, SearchRequest(use_current_location=True), deps=scenario.deps)
    assert result == NeedLocation()
    assert "candidates" not in await get_state(db_session, conv.id)


async def test_register_uses_closure(scenario, db_session, signed_up_user_id):
    scenario.add(PlaceFx("1", "가집", 1))
    reg = ToolRegistry()
    register(reg, scenario.deps)
    spec, handler = reg.get("search_restaurants")
    assert spec is SEARCH_RESTAURANTS
    conv = await get_or_create(db_session, signed_up_user_id, "web")
    ctx = ToolContext(user_id=signed_up_user_id, db=db_session, session_id=conv.id)
    result = await handler(ctx, SearchRequest(area_query="강남역"))
    assert isinstance(result, CandidateList)
    assert "candidates" in await get_state(db_session, conv.id)
