"""scripts/llm_ko_eval.py의 사례 파일 검증과 채점 로직 (외부 호출 없음)."""

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from dodeed.llm.types import LLMResponse, ToolCall

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_script():
    spec = importlib.util.spec_from_file_location("llm_ko_eval", SCRIPTS / "llm_ko_eval.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["llm_ko_eval"] = module
    spec.loader.exec_module(module)
    return module


ev = _load_script()


def _write(tmp_path: Path, cases: object) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    return path


def _call(**args) -> LLMResponse:
    return LLMResponse(
        text=None, tool_calls=[ToolCall(id="c1", name="search_restaurants", arguments=args)]
    )


def test_cases_file_valid():
    cases = ev.load_cases(ev.DEFAULT_CASES)
    assert len(cases) == 25
    for case in cases:
        assert isinstance(case, ev.Case)
        assert case.utterance.strip()
        assert case.tool in ("search_restaurants", None)
        if case.tool is None:
            assert case.args == {}
    # 계획의 구성: 상황 3종 각 5, 조합 5, 현재 위치 3, 잡담 2
    situations = Counter(c.args.get("situation") for c in cases if c.tool)
    assert situations["solo"] >= 5 and situations["date"] >= 5 and situations["value"] >= 5
    assert sum(1 for c in cases if c.args.get("use_current_location") is True) == 3
    assert sum(1 for c in cases if c.tool is None) == 2
    combos = [
        c for c in cases if {"area_query", "cuisine", "time_text", "party_size"} <= set(c.args)
    ]
    assert len(combos) == 5


@pytest.mark.parametrize(
    ("bad", "fragment"),
    [
        ({"x": 1}, "목록"),
        ([{"utterance": "", "expect": {"tool": None, "args": {}}}], "utterance"),
        ([{"utterance": "안녕"}], "expect"),
        ([{"utterance": "안녕", "expect": {"tool": "other", "args": {}}}], "tool"),
        ([{"utterance": "안녕", "expect": {"tool": None, "args": {"cuisine": "국밥"}}}], "args"),
        (
            [{"utterance": "x", "expect": {"tool": "search_restaurants", "args": {"zzz": 1}}}],
            "zzz",
        ),
        (
            [
                {
                    "utterance": "x",
                    "expect": {"tool": "search_restaurants", "args": {"situation": "brunch"}},
                }
            ],
            "situation",
        ),
    ],
)
def test_load_cases_rejects_bad_entries(tmp_path, bad, fragment):
    with pytest.raises(ValueError, match=fragment):
        ev.load_cases(_write(tmp_path, bad), expected_count=None)


def test_load_cases_checks_count(tmp_path):
    one = [{"utterance": "안녕", "expect": {"tool": None, "args": {}}}]
    with pytest.raises(ValueError, match="25"):
        ev.load_cases(_write(tmp_path, one))
    assert len(ev.load_cases(_write(tmp_path, one), expected_count=None)) == 1


def test_score_no_tool_expected():
    case = ev.Case("안녕", None, {})
    assert ev.score(case, LLMResponse(text="안녕하세요")) == (True, True)
    assert ev.score(case, _call(cuisine="국밥")) == (False, False)


def test_score_partial_args_match():
    case = ev.Case("x", "search_restaurants", {"situation": "solo", "area_query": "강남역"})
    resp = _call(situation="solo", area_query=" 강남역 ", cuisine="국밥")
    assert ev.score(case, resp) == (True, True)
    assert ev.score(case, _call(situation="date", area_query="강남역")) == (True, False)
    assert ev.score(case, _call(situation="solo")) == (True, False)  # 기대 인자 누락
    assert ev.score(case, LLMResponse(text="어디서 드실래요?")) == (False, False)


def test_score_normalizes_types():
    case = ev.Case(
        "x",
        "search_restaurants",
        {"use_current_location": True, "party_size": 4, "price_max": 10000},
    )
    assert ev.score(case, _call(use_current_location=True, party_size=4, price_max=10000)) == (
        True,
        True,
    )
    assert ev.score(case, _call(use_current_location="true", party_size="4", price_max=10000)) == (
        True,
        True,
    )
    assert ev.score(case, _call(use_current_location=False, party_size=4, price_max=10000)) == (
        True,
        False,
    )


def test_score_uses_matching_tool_call():
    case = ev.Case("x", "search_restaurants", {"cuisine": "국밥"})
    resp = LLMResponse(
        text=None,
        tool_calls=[
            ToolCall(id="a", name="other", arguments={}),
            ToolCall(id="b", name="search_restaurants", arguments={"cuisine": "국밥"}),
        ],
    )
    assert ev.score(case, resp) == (True, True)


def test_estimate_cost():
    assert ev.estimate_cost("gpt-6-luna", 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert ev.estimate_cost("gemini-3.5-flash-lite", 2_000_000, 400_000) == pytest.approx(1.60)
    assert ev.estimate_cost("unknown-model", 1, 1) is None


def test_report_has_summary_and_no_key(tmp_path):
    case = ev.Case("강남역 혼밥", "search_restaurants", {"situation": "solo"})
    result = ev.ModelResult(label="OpenAI", model="gpt-6-luna")
    result.add(
        case,
        _call(situation="solo"),
        latency_s=0.5,
        usage={"input_tokens": 10, "output_tokens": 2},
        error=None,
    )
    result.add(case, None, latency_s=1.5, usage=None, error="LLMProviderError")
    md = ev.render_report([case, case], [result])
    assert "| OpenAI (gpt-6-luna) | 1/2 | 1/2 | 1 | 1000 | 10 | 2 |" in md
    assert "LLMProviderError" in md
    assert "사용량 정보가 없어" not in md  # 오류 난 호출은 사용량 누락으로 세지 않는다
