"""후보 목록을 채널 공통 카드로 바꾼다 (REQ-RST-030)."""

from __future__ import annotations

from datetime import datetime, timedelta

from dodeed.channels.messages import Button, Card
from dodeed.restaurant.candidates import Candidate, CandidateList
from dodeed.restaurant.toilet import ToiletInfo

WAIT_FRESHNESS = timedelta(minutes=5)  # 계획 3의 대기 정보에서 사용
UNCHECKED = "확인 안 됨"
MORE_CANDIDATES = "more_candidates"
_LOCATION_TEXT = {"inside": "매장 안", "building_shared": "건물 공용"}


def _toilet_line(t: ToiletInfo) -> str:
    parts = []
    if t.location in _LOCATION_TEXT:
        parts.append(_LOCATION_TEXT[t.location])
    if t.gender_separated is not None:
        parts.append("남녀 분리" if t.gender_separated else "남녀 공용")
    if not parts:
        return f"화장실: {UNCHECKED}"
    line = "화장실: " + " · ".join(parts)
    if t.source_link:
        kind = "블로그" if "blog." in t.source_link else "웹"
        when = f"{t.source_date:%Y-%m} " if t.source_date else ""
        line += f" ({when}{kind})"
    if t.conflicting:
        line += " · 출처마다 다름"
    return line


def _card(c: Candidate) -> Card:
    walk = f"도보 {c.walk_min}분" if c.walk_min is not None else f"도보 {UNCHECKED}"
    st = c.open_status
    if st.open_now is True:
        open_line = f"영업 중 · {st.closes_at} 마감" if st.closes_at else "영업 중"
    elif st.open_now is False:
        open_line = "영업 종료"
    else:
        open_line = f"영업 여부: {UNCHECKED}"
    if c.taste.status == "ok":
        taste = f"맛(내돈내산 {c.taste.evidence_count}건): {c.taste.summary}"
    else:
        taste = "맛: 근거 부족"
    buttons = [Button(label="지도 보기", payload=c.place_url)] if c.place_url else []
    buttons.append(Button(label="다른 후보", payload=MORE_CANDIDATES))
    return Card(
        title=c.name,
        lines=[
            f"{c.category} · {walk}" if c.category else walk,
            f"가격대: {c.price_note or UNCHECKED}",
            open_line,
            "대기·예약: 준비 중",
            _toilet_line(c.toilet),
            taste,
            f"추천 이유: {c.reason}",
        ],
        buttons=buttons,
    )


def to_cards(cl: CandidateList, now: datetime) -> list[Card]:
    # now는 계획 3의 대기 정보 신선도(WAIT_FRESHNESS) 판단에 쓴다.
    return [_card(c) for c in cl.candidates]
