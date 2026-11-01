from datetime import date

from dodeed.restaurant.reviews import ReviewDoc
from dodeed.restaurant.toilet import ToiletExtract, ToiletInfo, toilet_info


class MapReader:
    """원문에 들어 있는 표시어로 ToiletExtract 결과를 돌려주는 가짜 리더."""

    def __init__(self, mapping: dict[str, ToiletExtract | None]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    async def extract(self, raw, schema, instruction):
        assert schema is ToiletExtract
        self.calls.append(raw)
        for marker, value in self.mapping.items():
            if marker in raw:
                return value
        return ToiletExtract(gender_separated=None, location="unknown")


async def test_latest_source_wins_and_conflict_flagged():
    reader = MapReader(
        {
            "OLD": ToiletExtract(gender_separated=False, location="building_shared"),
            "NEW": ToiletExtract(gender_separated=True, location="inside"),
        }
    )
    docs = [
        ReviewDoc("OLD 화장실은 건물 공용", "https://blog.naver.com/a/1", date(2025, 3, 1)),
        ReviewDoc(
            "NEW 리모델링 후 화장실 매장 안", "https://blog.naver.com/a/2", date(2026, 9, 12)
        ),
    ]
    info = await toilet_info(reader, docs)
    assert info == ToiletInfo(
        gender_separated=True,
        location="inside",
        source_link="https://blog.naver.com/a/2",
        source_date=date(2026, 9, 12),
        conflicting=True,
    )


async def test_agreeing_sources_not_conflicting():
    reader = MapReader({"화장실": ToiletExtract(gender_separated=True, location="inside")})
    docs = [
        ReviewDoc("체험단 방문, 화장실 매장 안", "https://blog.naver.com/a/1", date(2026, 1, 1)),
        ReviewDoc("화장실 깨끗", "https://blog.naver.com/a/2", date(2026, 2, 1)),
    ]
    info = await toilet_info(reader, docs)
    assert info.location == "inside" and info.conflicting is False
    assert info.source_link == "https://blog.naver.com/a/2"


async def test_no_info_is_unknown():
    reader = MapReader({})
    docs = [ReviewDoc("국물이 진해요", "https://blog.naver.com/a/1", date(2026, 1, 1))]
    info = await toilet_info(reader, docs)
    assert info == ToiletInfo(
        gender_separated=None, location="unknown", source_link=None, source_date=None
    )
    assert reader.calls == []  # '화장실'이 없는 문서는 리더에 넘기지 않는다


async def test_reader_failure_is_unknown():
    reader = MapReader({"화장실": None})
    docs = [ReviewDoc("화장실 언급", "https://blog.naver.com/a/1", date(2026, 1, 1))]
    info = await toilet_info(reader, docs)
    assert info.location == "unknown" and info.gender_separated is None


async def test_at_most_6_newest_docs_read():
    reader = MapReader({})
    docs = [
        ReviewDoc(f"화장실 문서{i:02d}", f"https://blog.naver.com/a/{i}", date(2026, 1, i + 1))
        for i in range(9)
    ]
    await toilet_info(reader, docs)
    assert len(reader.calls) == 6
    assert not any("문서00" in c or "문서02" in c for c in reader.calls)
    assert any("문서08" in c for c in reader.calls)


async def test_missing_field_filled_from_older_doc():
    reader = MapReader(
        {
            "OLD": ToiletExtract(gender_separated=True, location="unknown"),
            "NEW": ToiletExtract(gender_separated=None, location="inside"),
        }
    )
    docs = [
        ReviewDoc("OLD 화장실 남녀 분리", "https://blog.naver.com/a/1", date(2025, 1, 1)),
        ReviewDoc("NEW 화장실 매장 안", "https://example.com/w", date(2026, 1, 1)),
    ]
    info = await toilet_info(reader, docs)
    assert info.gender_separated is True and info.location == "inside"
    assert info.conflicting is False
    assert info.source_link == "https://example.com/w"
