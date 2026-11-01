import json
from datetime import date
from pathlib import Path

import pytest

from dodeed.llm.fake import FakeLLM
from dodeed.llm.types import LLMResponse
from dodeed.reader.isolated import IsolatedReader
from dodeed.restaurant.reviews import (
    MIN_TASTE_REVIEWS,
    ReviewDoc,
    ReviewKind,
    classify,
    taste_summary,
)

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "reviews" / "mixed.json"


def _load_mixed() -> list[dict]:
    return json.loads(FIX.read_text(encoding="utf-8"))


def _doc(row: dict) -> ReviewDoc:
    return ReviewDoc(
        text=row["text"],
        link=row["link"],
        posted=date.fromisoformat(row["posted"]) if row["posted"] else None,
        is_receipt_verified=row["is_receipt_verified"],
        body_read=True,
    )


def read(text: str, link: str, posted: date | None) -> ReviewDoc:
    """본문까지 읽은 블로그 문서."""
    return ReviewDoc(text, link, posted, body_read=True)


@pytest.fixture
def fixture_mixed_reviews() -> list[dict]:
    return _load_mixed()


@pytest.fixture
def fake_reader():
    llm = FakeLLM([LLMResponse('{"summary": "국물이 진해요"}')])
    return IsolatedReader(llm), llm


def test_sponsored_wins_over_own_money():
    assert classify("내돈내산 아니고 소정의 원고료를 받았습니다") == ReviewKind.SPONSORED


def test_sponsored_wins_even_when_receipt_verified():
    assert classify("체험단으로 다녀왔어요", is_receipt_verified=True) == ReviewKind.SPONSORED


def test_receipt_verified_is_own_money():
    assert classify("맛있어요", is_receipt_verified=True) == ReviewKind.OWN_MONEY


@pytest.mark.parametrize("text", ["내돈내산 후기", "내 돈 내 산 방문", "직접 결제했어요"])
def test_own_money_phrases(text):
    assert classify(text) == ReviewKind.OWN_MONEY


def test_no_phrase_is_unknown():
    assert classify("맛있는 국밥집") == ReviewKind.UNKNOWN


def test_mixed_fixture_labels_match(fixture_mixed_reviews):
    for row in fixture_mixed_reviews:
        assert classify(row["text"], row["is_receipt_verified"]) == ReviewKind(row["kind"]), row
    kinds = [row["kind"] for row in fixture_mixed_reviews]
    assert (kinds.count("sponsored"), kinds.count("own_money"), kinds.count("unknown")) == (5, 4, 6)


async def test_unknown_excluded_from_taste(fake_reader):
    reader, llm = fake_reader
    docs = [
        read("내돈내산 국물 진함", "https://blog.naver.com/a/1", date(2026, 10, 1)),
        read("직접 결제 김치 맛있음", "https://blog.naver.com/a/2", date(2026, 10, 2)),
        read("내 돈 내 산 수육 추천", "https://blog.naver.com/a/3", date(2026, 10, 3)),
        read("그냥 다녀온 국밥집 UNKNOWN-MARK", "https://blog.naver.com/a/4", None),
    ]
    result = await taste_summary(reader, docs)
    assert result.status == "ok" and result.evidence_count == 3
    sent = llm.calls[0].messages[-1].content
    assert "UNKNOWN-MARK" not in sent
    assert "https://blog.naver.com/a/4" not in result.source_links


async def test_taste_insufficient_below_3_without_calling_reader(fake_reader):
    reader, llm = fake_reader
    docs = [
        read("내돈내산 맛있음", "https://blog.naver.com/a/1", date(2026, 10, 1)),
        read("직접 결제 맛있음", "https://blog.naver.com/a/2", date(2026, 10, 2)),
        read("체험단 맛있음", "https://blog.naver.com/a/3", date(2026, 10, 3)),
    ]
    result = await taste_summary(reader, docs)
    assert MIN_TASTE_REVIEWS == 3
    assert result.status == "insufficient" and result.summary is None
    assert result.evidence_count == 2
    assert llm.calls == []


async def test_reader_failure_is_insufficient():
    llm = FakeLLM([LLMResponse("not json")])
    docs = [read(f"내돈내산 {i}", f"https://blog.naver.com/a/{i}", None) for i in range(3)]
    result = await taste_summary(IsolatedReader(llm), docs)
    assert result.status == "insufficient" and result.summary is None


async def test_no_sponsored_review_in_taste_evidence(fixture_mixed_reviews, fake_reader):
    reader, llm = fake_reader
    result = await taste_summary(reader, [_doc(r) for r in fixture_mixed_reviews])
    assert result.status == "ok" and result.summary == "국물이 진해요"
    assert result.evidence_count == 4
    assert len(llm.calls) == 1
    sent = llm.calls[0].messages[-1].content
    own = [r for r in fixture_mixed_reviews if r["kind"] == "own_money"]
    others = [r for r in fixture_mixed_reviews if r["kind"] != "own_money"]
    for r in own:
        assert r["text"] in sent
    for r in others:
        assert r["text"] not in sent
    assert sorted(result.source_links) == sorted(r["link"] for r in own)


async def test_taste_uses_at_most_10_newest():
    llm = FakeLLM([LLMResponse('{"summary": "좋아요"}')])
    docs = [
        read(f"내돈내산 리뷰번호{i:02d}", f"https://blog.naver.com/a/{i}", date(2026, 1, i + 1))
        for i in range(12)
    ]
    result = await taste_summary(IsolatedReader(llm), docs)
    assert result.evidence_count == 10
    sent = llm.calls[0].messages[-1].content
    assert "리뷰번호00" not in sent and "리뷰번호01" not in sent and "리뷰번호11" in sent


async def test_snippet_only_docs_not_used_for_taste(fake_reader):
    """본문을 못 읽은 문서는 대가성 표시가 본문에 숨어 있을 수 있어 맛 평가에서 뺀다."""
    reader, llm = fake_reader
    docs = [
        ReviewDoc(f"내돈내산 후기 {i}", f"https://blog.naver.com/a/{i}", date(2026, 10, i + 1))
        for i in range(5)
    ]
    result = await taste_summary(reader, docs)
    assert result.status == "insufficient" and result.evidence_count == 0
    assert llm.calls == []


async def test_summary_truncated_to_80_chars():
    llm = FakeLLM([LLMResponse(json.dumps({"summary": "가" * 200}))])
    docs = [read(f"내돈내산 {i}", f"https://blog.naver.com/a/{i}", None) for i in range(3)]
    result = await taste_summary(IsolatedReader(llm, max_text_field_chars=1000), docs)
    assert result.summary == "가" * 80
