"""후보 조회 파이프라인: 출발지 → 카카오 검색 → 보강 → 거르기 → 정렬 (REQ-RST-001/020/030)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from dodeed.reader.isolated import IsolatedReader
from dodeed.restaurant.hours import OpenStatus, PageText, open_status
from dodeed.restaurant.reviews import ReviewDoc, SourceKind, TasteSummary, taste_summary
from dodeed.restaurant.situations import SITUATIONS, Situation, SituationProfile, format_won
from dodeed.restaurant.toilet import UNKNOWN as TOILET_UNKNOWN
from dodeed.restaurant.toilet import ToiletInfo, toilet_info
from dodeed.security.egress import EgressBlocked
from dodeed.sources import SourceUnavailable
from dodeed.sources.geo import walking_minutes
from dodeed.sources.kakao_local import KakaoLocal, Place
from dodeed.sources.localdata import LocalData
from dodeed.sources.naver_search import NaverSearch, SearchItem
from dodeed.sources.public_page import DomainNotAllowed, PublicPageReader

logger = logging.getLogger(__name__)

SEARCH_RADIUS_M = 1000
MIN_SEARCH_RESULTS = 3
ENRICH_TOP = 8
MAX_CANDIDATES = 5
BLOG_DISPLAY = 20
BLOG_BODY_TOP = 5
PAGE_CONCURRENCY = 6  # RestaurantDeps 하나(앱 전체)가 공유하는 동시 페이지 읽기 수
ENRICH_DEADLINE_S = 30.0  # 보강 단계 전체 마감. 넘기면 끝나지 않은 항목은 확인 안 됨

NOTICE_KAKAO_DOWN = "장소 검색 서비스에 일시적으로 연결할 수 없어요. 잠시 후 다시 시도해 주세요."
NOTICE_NO_RESULTS = (
    "조건에 맞는 곳을 찾지 못했어요. 반경을 넓히거나 조건을 하나 빼서 다시 찾아볼까요?"
)
NOTICE_AREA_NOT_FOUND = "말씀하신 곳을 찾지 못했어요. 지명을 조금 더 구체적으로 알려주시겠어요?"
DEFAULT_TIME = "시간: 지금"
CURRENT_LOCATION = "현재 위치"
TOILET_ORDER = {"inside": 0, "unknown": 1, "building_shared": 2}


class SearchRequest(BaseModel):
    situation: Situation = Field(
        Situation.NONE,
        description="상황: solo(혼밥), date(데이트), value(가성비), none(특별한 상황 없음)",
    )
    area_query: str | None = Field(None, description='사용자가 말한 지명, 예: "강남역"')
    use_current_location: bool = Field(
        False, description='"지금 여기", "근처"처럼 현재 위치를 기준으로 할 때 true'
    )
    cuisine: str | None = Field(None, description='음식 종류, 예: "국밥", "파스타"')
    price_max: int | None = Field(None, description="1인 최대 가격(원)")
    toilet_inside: bool = Field(False, description="화장실이 매장 안에 있는 곳을 원하면 true")
    party_size: int | None = Field(None, description="인원 수")
    time_text: str | None = Field(None, description='사용자가 말한 시간 그대로, 예: "오늘 7시"')


class Candidate(BaseModel):
    place_id: str
    name: str
    category: str
    place_url: str = ""
    walk_min: int | None
    open_status: OpenStatus
    closed_permanently: bool | None
    toilet: ToiletInfo
    taste: TasteSummary
    price_note: str | None
    reason: str
    waiting: None = None  # 대기·예약 정보는 계획 3에서 채운다


class CandidateList(BaseModel):
    request: SearchRequest
    origin_area: str
    candidates: list[Candidate]
    defaults_used: list[str]
    notice: str | None


class NeedLocation(BaseModel):
    kind: Literal["need_location"] = "need_location"


@dataclass
class RestaurantDeps:
    kakao: KakaoLocal
    naver: NaverSearch
    localdata: LocalData
    reader: IsolatedReader
    page_reader: PublicPageReader
    page_slots: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(PAGE_CONCURRENCY)
    )


class _LimitedPages:
    """공개 페이지 동시 읽기 수를 제한한다 (RestaurantDeps.page_slots 공유)."""

    def __init__(self, inner: PageText, sem: asyncio.Semaphore) -> None:
        self._inner = inner
        self._sem = sem

    async def text(self, url: str, timeout_s: float = 15) -> str | None:
        async with self._sem:
            return await self._inner.text(url, timeout_s)


async def _gather_safe(*aws: Awaitable[Any]) -> list[Any]:
    """병렬 실행 후 실패한 항목은 None으로 바꾼다. EgressBlocked와 취소는 다시 던진다."""
    results = await asyncio.gather(*aws, return_exceptions=True)
    for r in results:
        if isinstance(r, EgressBlocked) or (
            isinstance(r, BaseException) and not isinstance(r, Exception)
        ):
            raise r
    out = []
    for r in results:
        if isinstance(r, Exception):
            if not isinstance(r, SourceUnavailable | DomainNotAllowed):
                logger.warning("음식점 보강 단계 실패: %s", type(r).__name__)
            out.append(None)
        else:
            out.append(r)
    return out


def district(road_address: str) -> str:
    """도로명 주소에서 구·동 토큰을 고른다. 없으면 앞의 두 토큰."""
    tokens = road_address.split()
    picked = [t for t in tokens if t.endswith(("구", "동"))]
    return " ".join(picked or tokens[:2])


def _short_category(category: str) -> str:
    return category.split(">")[-1].strip() if category else ""


def _doc_from(item: SearchItem, kind: SourceKind, body: str | None = None) -> ReviewDoc:
    text = "\n".join(t for t in (item.title, item.description, body) if t)
    return ReviewDoc(
        text=text, link=item.link, posted=item.postdate, source_kind=kind, body_read=bool(body)
    )


async def _body(pages: PageText, url: str) -> str | None:
    try:
        return await pages.text(url)
    except DomainNotAllowed:
        return None


async def _blog_docs(deps: RestaurantDeps, pages: PageText, place: Place) -> list[ReviewDoc]:
    query = f"{place.name} {district(place.road_address)}".strip()
    items = await deps.naver.search_blog(query, display=BLOG_DISPLAY)
    bodies = await _gather_safe(*(_body(pages, i.link) for i in items[:BLOG_BODY_TOP]))
    bodies += [None] * (len(items) - len(bodies))
    return [_doc_from(i, "blog", b) for i, b in zip(items, bodies, strict=True)]


async def _web_docs(deps: RestaurantDeps, place: Place) -> list[ReviewDoc]:
    return [_doc_from(i, "web") for i in await deps.naver.search_web(f"{place.name} 화장실")]


@dataclass
class _Enriched:
    """보강 결과. 각 단계가 끝나는 대로 채우므로 마감 시각에 걸려도 끝난 값은 남는다."""

    place: Place
    hours: OpenStatus
    status: str = "unknown"
    blog_docs: list[ReviewDoc] = field(default_factory=list)
    web_docs: list[ReviewDoc] = field(default_factory=list)
    toilet: ToiletInfo = field(default_factory=lambda: TOILET_UNKNOWN.model_copy())
    taste: TasteSummary = field(
        default_factory=lambda: TasteSummary(
            status="insufficient", summary=None, evidence_count=0, source_links=[]
        )
    )

    @property
    def excluded(self) -> bool:
        return self.status == "closed" or self.hours.open_now is False


async def _enrich(deps: RestaurantDeps, pages: PageText, e: _Enriched, now: datetime) -> None:
    place = e.place

    async def status() -> None:
        e.status = await deps.localdata.business_status(place.name, place.road_address)

    async def hours() -> None:
        e.hours = await open_status(deps.reader, pages, place, now)

    async def blogs() -> None:
        e.blog_docs = await _blog_docs(deps, pages, place)

    async def web() -> None:
        e.web_docs = await _web_docs(deps, place)

    await _gather_safe(status(), hours(), blogs(), web())
    if e.excluded:  # 뺄 곳은 리더를 더 부르지 않는다
        return

    async def toilet() -> None:
        e.toilet = await toilet_info(deps.reader, e.blog_docs + e.web_docs)

    async def taste() -> None:
        e.taste = await taste_summary(deps.reader, e.blog_docs)

    await _gather_safe(toilet(), taste())


def _matched_keywords(profile: SituationProfile | None, e: _Enriched) -> list[str]:
    if profile is None:
        return []
    texts = [e.place.category, *(d.text for d in e.blog_docs)]
    return [kw for kw in profile.keywords if any(kw in t for t in texts)]


def _reason(req: SearchRequest, profile: SituationProfile | None, matched: list[str]) -> str:
    price = req.price_max or (profile.price_max if profile else None)
    price_cond = f"{format_won(price)} 이하" if price else None
    if profile is None:
        conds = [c for c in (req.cuisine, price_cond) if c]
        return ", ".join(conds) or "가까운 순"
    detail = [*matched, *([price_cond] if price_cond else [])]
    return f"{profile.label}: {', '.join(detail)}" if detail else profile.label


async def _search_places(
    kakao: KakaoLocal, req: SearchRequest, profile: SituationProfile | None, lat: float, lng: float
) -> list[Place]:
    """카카오 키워드 검색. 결과가 적으면 더 넓은 검색어로 한 번 더 찾는다."""
    terms = [req.cuisine, profile.keywords[0] if profile else None]
    query = " ".join(t for t in terms if t) or "맛집"
    places = await kakao.search_keyword(query, x=lng, y=lat, radius_m=SEARCH_RADIUS_M)
    retry_query = req.cuisine or "음식점"
    if len(places) < MIN_SEARCH_RESULTS and retry_query != query:
        try:
            more = await kakao.search_keyword(retry_query, x=lng, y=lat, radius_m=SEARCH_RADIUS_M)
        except SourceUnavailable:
            more = []
        seen = {p.id for p in places}
        places += [p for p in more if p.id not in seen]
    return places


async def _area_name(kakao: KakaoLocal, location: dict) -> str:
    if location.get("area_name"):
        return location["area_name"]
    try:
        return await kakao.region_name(location["lat"], location["lng"]) or CURRENT_LOCATION
    except SourceUnavailable:
        return CURRENT_LOCATION


async def find_candidates(
    deps: RestaurantDeps, req: SearchRequest, location: dict | None, now: datetime
) -> CandidateList | NeedLocation:
    defaults = [] if req.time_text else [DEFAULT_TIME]

    def result(origin_area: str, cands: list[Candidate], notice: str | None) -> CandidateList:
        return CandidateList(
            request=req,
            origin_area=origin_area,
            candidates=cands,
            defaults_used=defaults,
            notice=notice,
        )

    # 1. 출발 위치
    if req.use_current_location or not req.area_query:
        if not location:
            return NeedLocation()
        lat, lng = float(location["lat"]), float(location["lng"])
        origin_area = await _area_name(deps.kakao, location)
    else:
        try:
            geo = await deps.kakao.geocode_area(req.area_query)
        except SourceUnavailable:
            return result(req.area_query, [], NOTICE_KAKAO_DOWN)
        if geo is None:
            return result(req.area_query, [], NOTICE_AREA_NOT_FOUND)
        lat, lng, origin_area = geo

    # 2. 카카오 키워드 검색
    profile = SITUATIONS.get(req.situation)
    try:
        places = await _search_places(deps.kakao, req, profile, lat, lng)
    except SourceUnavailable:
        return result(origin_area, [], NOTICE_KAKAO_DOWN)

    # 3. 상위 8곳 병렬 보강 (공개 페이지 동시 읽기 제한, 전체 마감 시각)
    pages = _LimitedPages(deps.page_reader, deps.page_slots)
    unknown_hours = OpenStatus(open_now=None, closes_at=None, checked_at=now)
    enriched = [_Enriched(place=p, hours=unknown_hours) for p in places[:ENRICH_TOP]]
    try:
        async with asyncio.timeout(ENRICH_DEADLINE_S):
            await _gather_safe(*(_enrich(deps, pages, e, now) for e in enriched))
    except TimeoutError:
        logger.warning("음식점 보강 마감(%ss) 초과: 끝난 항목만 씁니다", ENRICH_DEADLINE_S)

    # 4. 폐업·영업 종료 제외, 5. 정렬
    rows = []
    for e in enriched:
        if e.excluded:
            continue
        matched = _matched_keywords(profile, e)
        walk = walking_minutes(lat, lng, e.place.lat, e.place.lng)
        cand = Candidate(
            place_id=e.place.id,
            name=e.place.name,
            category=_short_category(e.place.category),
            place_url=e.place.place_url,
            walk_min=walk,
            open_status=e.hours,
            closed_permanently={"closed": True, "open": False}.get(e.status),
            toilet=e.toilet,
            taste=e.taste,
            price_note=None,
            reason=_reason(req, profile, matched),
        )
        toilet_rank = TOILET_ORDER[e.toilet.location] if req.toilet_inside else 0
        rows.append(((toilet_rank, 0 if matched else 1, walk), cand))
    rows.sort(key=lambda r: r[0])

    # 6. 상위 5곳
    cands = [c for _, c in rows[:MAX_CANDIDATES]]
    return result(origin_area, cands, None if cands else NOTICE_NO_RESULTS)
