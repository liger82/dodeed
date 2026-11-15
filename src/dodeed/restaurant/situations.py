"""상황 표현을 검색 조건으로 바꾼다 (REQ-RST-001)."""

from dataclasses import dataclass
from enum import StrEnum


class Situation(StrEnum):
    SOLO = "solo"
    DATE = "date"
    VALUE = "value"
    NONE = "none"


@dataclass(frozen=True)
class SituationProfile:
    label: str
    price_max: int | None
    keywords: tuple[str, ...]


SITUATIONS: dict[Situation, SituationProfile] = {
    Situation.SOLO: SituationProfile("혼밥", 15000, ("혼밥", "1인석", "바 좌석", "카운터석")),
    Situation.DATE: SituationProfile("데이트", None, ("분위기", "데이트", "조용한")),
    Situation.VALUE: SituationProfile("가성비", 12000, ("가성비", "양 많", "저렴")),
}


def format_won(amount: int) -> str:
    """15000 → "1만 5천 원", 10000 → "1만 원", 8000 → "8천 원"."""
    man, rest = divmod(amount, 10000)
    cheon, won = divmod(rest, 1000)
    parts = []
    if man:
        parts.append(f"{man}만")
    if cheon:
        parts.append(f"{cheon}천")
    if won or not parts:
        parts.append(str(won))
    return " ".join(parts) + " 원"
