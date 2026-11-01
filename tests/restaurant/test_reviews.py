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
    )


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
        ReviewDoc("내돈내산 국물 진함", "https://blog.naver.com/a/1", date(2026, 10, 1)),
        ReviewDoc("직접 결제 김치 맛있음", "https://blog.naver.com/a/2", date(2026, 10, 2)),
        ReviewDoc("내 돈 내 산 수육 추천", "https://blog.naver.com/a/3", date(2026, 10, 3)),
        ReviewDoc("그냥 다녀온 국밥집 UNKNOWN-MARK", "https://blog.naver.com/a/4", None),
    ]
    result = await taste_summary(reader, docs)
    assert result.status == "ok" and result.evidence_count == 3
    sent = llm.calls[0].messages[-1].content
    assert "UNKNOWN-MARK" not in sent
    assert "https://blog.naver.com/a/4" not in result.source_links


async def test_taste_insufficient_below_3_without_calling_reader(fake_reader):
    reader, llm = fake_reader
    docs = [
        ReviewDoc("내돈내산 맛있음", "https://blog.naver.com/a/1", date(2026, 10, 1)),
        ReviewDoc("직접 결제 맛있음", "https://blog.naver.com/a/2", date(2026, 10, 2)),
        ReviewDoc("체험단 맛있음", "https://blog.naver.com/a/3", date(2026, 10, 3)),
    ]
    result = await taste_summary(reader, docs)
    assert MIN_TASTE_REVIEWS == 3
    assert result.status == "insufficient" and result.summary is None
    assert result.evidence_count == 2
    assert llm.calls == []


async def test_reader_failure_is_insufficient():
    llm = FakeLLM([LLMResponse("not json")])
    docs = [ReviewDoc(f"내돈내산 {i}", f"https://blog.naver.com/a/{i}", None) for i in range(3)]
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
        ReviewDoc(
            f"내돈내산 리뷰번호{i:02d}", f"https://blog.naver.com/a/{i}", date(2026, 1, i + 1)
        )
        for i in range(12)
    ]
    result = await taste_summary(IsolatedReader(llm), docs)
    assert result.evidence_count == 10
    sent = llm.calls[0].messages[-1].content
    assert "리뷰번호00" not in sent and "리뷰번호01" not in sent and "리뷰번호11" in sent
