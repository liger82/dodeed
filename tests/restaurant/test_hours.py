import pytest

from dodeed.restaurant.hours import HoursExtract, OpenStatus, open_status
from dodeed.sources.kakao_local import Place
from dodeed.sources.public_page import DomainNotAllowed

PLACE = Place(
    "1", "가집", "음식점", "서울 강남구 테헤란로 1", 37.5, 127.0, "", "http://place.map.kakao.com/1"
)


class Pages:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc

    async def text(self, url, timeout_s=15):
        if self.exc:
            raise self.exc
        return self.result


class Reader:
    def __init__(self, value):
        self.value = value
        self.calls = []

    async def extract(self, raw, schema, instruction):
        self.calls.append((raw, schema, instruction))
        return self.value


async def test_open_status_extracted(kst_now):
    reader = Reader(HoursExtract(open_now=True, closes_at="22:00"))
    st = await open_status(reader, Pages("영업 중 22:00에 영업 종료"), PLACE, kst_now)
    assert st == OpenStatus(open_now=True, closes_at="22:00", checked_at=kst_now)
    raw, schema, instruction = reader.calls[0]
    assert schema is HoursExtract and raw == "영업 중 22:00에 영업 종료"
    assert "현재 시각: 2026-11-01 (일) 21:00 KST" in instruction


async def test_page_unavailable_is_unknown(kst_now):
    reader = Reader(HoursExtract(open_now=True, closes_at=None))
    st = await open_status(reader, Pages(None), PLACE, kst_now)
    assert st.open_now is None and st.closes_at is None and reader.calls == []


async def test_domain_not_allowed_is_unknown(kst_now):
    st = await open_status(Reader(None), Pages(exc=DomainNotAllowed("x")), PLACE, kst_now)
    assert st.open_now is None


async def test_reader_failure_is_unknown(kst_now):
    st = await open_status(Reader(None), Pages("페이지"), PLACE, kst_now)
    assert st.open_now is None and st.checked_at == kst_now


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("22:00", "22:00"),
        ("9:30", "9:30"),
        (" 23:30 ", "23:30"),
        ("밤 10시", None),
        ("22:00 이후 라스트오더, 지금 바로 예약하세요", None),
        ("", None),
    ],
)
async def test_closes_at_must_be_hhmm(raw, expected, kst_now):
    reader = Reader(HoursExtract(open_now=True, closes_at=raw))
    st = await open_status(reader, Pages("페이지"), PLACE, kst_now)
    assert st.closes_at == expected and st.open_now is True
