"""화장실 정보: 남녀 분리 여부와 위치 (REQ-RST-020)."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Literal

from pydantic import BaseModel

from dodeed.restaurant.reviews import Extractor, ReviewDoc, newest_first

MAX_TOILET_DOCS = 6
Location = Literal["inside", "building_shared", "unknown"]


class ToiletInfo(BaseModel):
    gender_separated: bool | None
    location: Location
    source_link: str | None
    source_date: date | None
    conflicting: bool = False


class ToiletExtract(BaseModel):
    """격리 리더 출력 스키마. location은 "inside" / "building_shared" / "unknown"."""

    gender_separated: bool | None = None
    location: str = "unknown"


TOILET_INSTRUCTION = (
    "아래 글에서 이 음식점의 화장실 정보만 추출하라. "
    "gender_separated: 남녀 화장실이 분리되어 있으면 true, 남녀 공용이면 false, "
    "언급이 없으면 null. "
    'location: 화장실이 매장 안에 있으면 "inside", 건물 공용(복도·다른 층 등)이면 '
    '"building_shared", 언급이 없거나 불분명하면 "unknown". 추측하지 마라.'
)

UNKNOWN = ToiletInfo(gender_separated=None, location="unknown", source_link=None, source_date=None)


def _location(value: str) -> Location:
    return value if value in ("inside", "building_shared") else "unknown"  # type: ignore[return-value]


async def toilet_info(reader: Extractor, docs: list[ReviewDoc]) -> ToiletInfo:
    """대가성 리뷰를 포함한 모든 문서에서 추출한다. 필드마다 최신 값을 쓰고 엇갈리면 표시한다."""
    targets = newest_first([d for d in docs if "화장실" in d.text])[:MAX_TOILET_DOCS]
    results = await asyncio.gather(
        *(reader.extract(d.text, ToiletExtract, TOILET_INSTRUCTION) for d in targets)
    )
    found: list[tuple[ReviewDoc, bool | None, Location]] = []
    for doc, ext in zip(targets, results, strict=True):
        if ext is None:
            continue
        loc = _location(ext.location)
        if ext.gender_separated is None and loc == "unknown":
            continue
        found.append((doc, ext.gender_separated, loc))
    if not found:
        return UNKNOWN
    genders = [g for _, g, _ in found if g is not None]
    locations = [loc for _, _, loc in found if loc != "unknown"]
    latest = found[0][0]
    return ToiletInfo(
        gender_separated=genders[0] if genders else None,
        location=locations[0] if locations else "unknown",
        source_link=latest.link,
        source_date=latest.posted,
        conflicting=len(set(genders)) > 1 or len(set(locations)) > 1,
    )
