"""현재 영업 여부: 카카오 공개 장소 페이지를 격리 리더로 읽는다."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from dodeed.restaurant.reviews import Extractor
from dodeed.sources.kakao_local import Place
from dodeed.sources.public_page import DomainNotAllowed

KST = ZoneInfo("Asia/Seoul")
_WEEKDAYS = "월화수목금토일"


class PageText(Protocol):
    async def text(self, url: str, timeout_s: float = 15) -> str | None: ...


class OpenStatus(BaseModel):
    open_now: bool | None
    closes_at: str | None
    checked_at: datetime


class HoursExtract(BaseModel):
    """격리 리더 출력 스키마."""

    open_now: bool | None = None
    closes_at: str | None = None


def kst_label(now: datetime) -> str:
    k = now.astimezone(KST)
    return f"{k:%Y-%m-%d} ({_WEEKDAYS[k.weekday()]}) {k:%H:%M} KST"


def hours_instruction(now: datetime) -> str:
    return (
        f"현재 시각: {kst_label(now)}\n"
        "아래는 음식점 장소 페이지 내용이다. 영업 시간과 휴무일을 보고 현재 시각에 영업 중인지 "
        "판단해 open_now(영업 중이면 true, 아니면 false, 판단할 수 없으면 null)와 "
        'closes_at(오늘 영업 종료 시각, "HH:MM" 형식, 모르면 null)을 채워라.'
    )


async def open_status(
    reader: Extractor, page_reader: PageText, place: Place, now: datetime
) -> OpenStatus:
    unknown = OpenStatus(open_now=None, closes_at=None, checked_at=now)
    if not place.place_url:
        return unknown
    try:
        text = await page_reader.text(place.place_url)
    except DomainNotAllowed:
        return unknown
    if not text:
        return unknown
    ext = await reader.extract(text, HoursExtract, hours_instruction(now))
    if ext is None:
        return unknown
    return OpenStatus(open_now=ext.open_now, closes_at=ext.closes_at, checked_at=now)
