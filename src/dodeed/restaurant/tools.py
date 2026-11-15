"""search_restaurants 도구 등록."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from dodeed.policy.tools import Grade, ToolContext, ToolRegistry, ToolSpec
from dodeed.restaurant.candidates import (
    CandidateList,
    NeedLocation,
    RestaurantDeps,
    SearchRequest,
    find_candidates,
)
from dodeed.sessions.service import get_state, set_state

SEARCH_RESTAURANTS = ToolSpec(
    name="search_restaurants",
    service="dodeed",
    grade=Grade.READ,
    input_model=SearchRequest,
    description=(
        "출발 위치 주변(도보 1km)의 음식점 후보를 찾는다. 상황(혼밥·데이트·가성비), 음식 종류, "
        "가격, 화장실 조건을 반영하고 영업 여부·화장실·내돈내산 맛 요약을 함께 돌려준다. "
        "현재 위치가 필요한데 없으면 need_location을 돌려준다."
    ),
)


async def handle_search(
    ctx: ToolContext,
    req: SearchRequest,
    *,
    deps: RestaurantDeps,
    now: datetime | None = None,
) -> CandidateList | NeedLocation:
    """후보를 찾고, 목록이면 세션 상태 "candidates"에 저장한다 (다른 상태 값은 유지)."""
    state = await get_state(ctx.db, ctx.session_id)
    result = await find_candidates(
        deps, req, state.get("location"), now if now is not None else datetime.now(UTC)
    )
    if isinstance(result, CandidateList):
        await set_state(
            ctx.db, ctx.session_id, {**state, "candidates": result.model_dump(mode="json")}
        )
    return result


def register(registry: ToolRegistry, deps: RestaurantDeps) -> None:
    async def handler(ctx: ToolContext, req: BaseModel) -> BaseModel:
        assert isinstance(req, SearchRequest)
        return await handle_search(ctx, req, deps=deps)

    registry.register(SEARCH_RESTAURANTS, handler)
