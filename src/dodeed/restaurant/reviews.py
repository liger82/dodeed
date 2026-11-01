"""리뷰 분류와 내돈내산 리뷰 기반 맛 요약 (REQ-RST-010)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Extractor(Protocol):
    async def extract(self, raw: str, schema: type[T], instruction: str) -> T | None: ...


class ReviewKind(StrEnum):
    SPONSORED = "sponsored"
    OWN_MONEY = "own_money"
    UNKNOWN = "unknown"


SPONSORED_PHRASES = ("소정의 원고료", "원고료를 받", "제공받아", "제공 받아", "체험단", "협찬")
OWN_MONEY_PHRASES = ("내돈내산", "내 돈 내 산", "직접 결제")
MIN_TASTE_REVIEWS = 3
MAX_TASTE_REVIEWS = 10


@dataclass
class ReviewDoc:
    text: str
    link: str
    posted: date | None
    is_receipt_verified: bool = False


class TasteSummary(BaseModel):
    status: Literal["ok", "insufficient"]
    summary: str | None
    evidence_count: int
    source_links: list[str]


class TasteExtract(BaseModel):
    """격리 리더 출력 스키마."""

    summary: str


TASTE_INSTRUCTION = (
    "아래는 한 음식점에 대한 내돈내산 리뷰 모음이다. 음식의 맛에 대한 공통된 평가를 "
    "한국어 한 문장(60자 이내)으로 요약해 summary에 넣어라. 리뷰에 없는 내용은 지어내지 마라."
)


def classify(text: str, is_receipt_verified: bool = False) -> ReviewKind:
    """대가성 문구가 있으면 무조건 SPONSORED, 그다음 내돈내산 문구나 영수증 인증이면 OWN_MONEY."""
    if any(p in text for p in SPONSORED_PHRASES):
        return ReviewKind.SPONSORED
    if is_receipt_verified or any(p in text for p in OWN_MONEY_PHRASES):
        return ReviewKind.OWN_MONEY
    return ReviewKind.UNKNOWN


def newest_first(docs: list[ReviewDoc]) -> list[ReviewDoc]:
    """작성일 최신순. 작성일을 모르는 문서는 뒤로 보낸다."""
    return sorted(docs, key=lambda d: (d.posted is not None, d.posted or date.min), reverse=True)


async def taste_summary(reader: Extractor, reviews: list[ReviewDoc]) -> TasteSummary:
    own = [r for r in reviews if classify(r.text, r.is_receipt_verified) == ReviewKind.OWN_MONEY]
    if len(own) < MIN_TASTE_REVIEWS:
        return TasteSummary(
            status="insufficient", summary=None, evidence_count=len(own), source_links=[]
        )
    used = newest_first(own)[:MAX_TASTE_REVIEWS]
    raw = "\n\n".join(f"[리뷰 {i + 1}]\n{r.text}" for i, r in enumerate(used))
    result = await reader.extract(raw, TasteExtract, TASTE_INSTRUCTION)
    if result is None or not result.summary.strip():
        return TasteSummary(
            status="insufficient", summary=None, evidence_count=len(used), source_links=[]
        )
    return TasteSummary(
        status="ok",
        summary=result.summary.strip(),
        evidence_count=len(used),
        source_links=[r.link for r in used],
    )
