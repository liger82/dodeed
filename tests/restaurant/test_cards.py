from datetime import date, timedelta

from dodeed.channels.messages import Button, Card
from dodeed.restaurant.candidates import Candidate, CandidateList, SearchRequest
from dodeed.restaurant.cards import WAIT_FRESHNESS, to_cards
from dodeed.restaurant.hours import OpenStatus
from dodeed.restaurant.reviews import TasteSummary
from dodeed.restaurant.toilet import ToiletInfo


def make(now, **kw) -> Candidate:
    base = dict(
        place_id="1001",
        name="을지식당",
        category="국밥",
        place_url="http://place.map.kakao.com/1001",
        walk_min=4,
        open_status=OpenStatus(open_now=True, closes_at="22:00", checked_at=now),
        closed_permanently=False,
        toilet=ToiletInfo(
            gender_separated=True,
            location="inside",
            source_link="https://blog.naver.com/a/1",
            source_date=date(2026, 9, 12),
            source_kind="blog",
        ),
        taste=TasteSummary(
            status="ok", summary="국물이 진해요", evidence_count=7, source_links=["x"]
        ),
        price_note=None,
        reason="혼밥: 1인석, 1만 5천 원 이하",
    )
    base.update(kw)
    return Candidate(**base)


def cl_of(*cands) -> CandidateList:
    return CandidateList(
        request=SearchRequest(),
        origin_area="강남역",
        candidates=list(cands),
        defaults_used=[],
        notice=None,
    )


def test_wait_freshness():
    assert WAIT_FRESHNESS == timedelta(minutes=5)


def test_card_lines_format(now):
    cards = to_cards(cl_of(make(now)), now)
    assert cards == [
        Card(
            title="을지식당",
            lines=[
                "국밥 · 도보 4분",
                "가격대: 확인 안 됨",
                "영업 중 · 22:00 마감 (21:00 확인)",
                "대기·예약: 준비 중",
                "화장실: 매장 안 · 남녀 분리 (2026-09 블로그)",
                "맛(내돈내산 7건): 국물이 진해요",
                "추천 이유: 혼밥: 1인석, 1만 5천 원 이하",
                "정보 조회: 21:00",
            ],
            buttons=[
                Button(label="지도 보기", payload="http://place.map.kakao.com/1001"),
                Button(label="다른 후보", payload="more_candidates"),
            ],
        )
    ]


def test_card_unknown_values(now):
    c = make(
        now,
        walk_min=None,
        open_status=OpenStatus(open_now=None, closes_at=None, checked_at=now),
        toilet=ToiletInfo(
            gender_separated=None, location="unknown", source_link=None, source_date=None
        ),
        taste=TasteSummary(status="insufficient", summary=None, evidence_count=1, source_links=[]),
    )
    lines = to_cards(cl_of(c), now)[0].lines
    assert lines[0] == "국밥 · 도보 확인 안 됨"
    assert lines[2] == "영업 여부: 확인 안 됨"
    assert lines[4] == "화장실: 확인 안 됨"
    assert lines[5] == "맛: 근거 부족"


def test_card_toilet_conflict_and_web_source(now):
    c = make(
        now,
        toilet=ToiletInfo(
            gender_separated=False,
            location="building_shared",
            source_link="https://example.com/post",
            source_date=date(2026, 3, 2),
            source_kind="web",
            conflicting=True,
        ),
    )
    assert to_cards(cl_of(c), now)[0].lines[4] == (
        "화장실: 건물 공용 · 남녀 공용 (2026-03 웹) · 출처마다 다름"
    )


def test_card_toilet_without_date(now):
    c = make(
        now,
        toilet=ToiletInfo(
            gender_separated=None,
            location="inside",
            source_link="https://m.blog.naver.com/a/1",
            source_date=None,
        ),
    )
    assert to_cards(cl_of(c), now)[0].lines[4] == "화장실: 매장 안 (블로그)"


def test_card_open_without_closing_and_closed(now):
    c1 = make(now, open_status=OpenStatus(open_now=True, closes_at=None, checked_at=now))
    c2 = make(now, open_status=OpenStatus(open_now=False, closes_at=None, checked_at=now))
    cards = to_cards(cl_of(c1, c2), now)
    assert cards[0].lines[2] == "영업 중 (21:00 확인)"
    assert cards[1].lines[2] == "영업 종료 (21:00 확인)"


def test_checked_times_in_kst(now):
    checked = now - timedelta(minutes=7)  # 11:53 UTC = 20:53 KST
    c = make(now, open_status=OpenStatus(open_now=True, closes_at="22:00", checked_at=checked))
    lines = to_cards(cl_of(c), now)[0].lines
    assert lines[2] == "영업 중 · 22:00 마감 (20:53 확인)"
    assert lines[-1] == "정보 조회: 21:00"


def test_card_price_note(now):
    assert to_cards(cl_of(make(now, price_note="1만 원대")), now)[0].lines[1] == "가격대: 1만 원대"
