"""한국어 품질 비교: 같은 사례를 1차(OpenAI)와 백업(Gemini) 모델에 보내고 결과를 표로 쓴다.

실행:
    uv run python scripts/llm_ko_eval.py --out docs/superpowers/eval/2026-11-ko-llm.md

키는 환경변수 `DODEED_OPENAI_API_KEY`, `DODEED_GEMINI_API_KEY`(모델 이름은 선택
`DODEED_OPENAI_MODEL`, `DODEED_GEMINI_MODEL`)나 현재 폴더의 `.env`에서 읽는다.
키가 있는 모델만 돌리고, 둘 다 없으면 안내를 출력하고 종료 코드 2로 끝난다.
요청은 모두 GuardedLLM(출구 필터)을 거치며, 보고서와 콘솔에는 키를 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from dodeed.agent.prompts import SYSTEM
from dodeed.llm.gemini import GeminiClient
from dodeed.llm.guarded import GuardedLLM
from dodeed.llm.openai_chat import OpenAIChatClient
from dodeed.llm.types import LLMResponse, Message, ToolDef
from dodeed.restaurant.candidates import SearchRequest
from dodeed.restaurant.tools import SEARCH_RESTAURANTS
from dodeed.security.egress import SecretRegistry

DEFAULT_CASES = Path(__file__).resolve().parent / "llm_ko_eval_cases.json"
EXPECTED_CASES = 25
TIMEOUT_S = 60.0
TOOL_NAME = SEARCH_RESTAURANTS.name

# 1M 토큰당 (입력, 출력) 단가, USD. 공급자 가격표가 바뀌면 여기를 고친다.
PRICES_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-6-luna": (0.10, 0.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
}


@dataclass(frozen=True)
class Case:
    utterance: str
    tool: str | None
    args: dict[str, Any]


# --- 사례 파일 ---


def _validate_args(i: int, args: dict[str, Any]) -> None:
    fields = SearchRequest.model_fields
    for key in args:
        if key not in fields:
            raise ValueError(f"사례 {i}: args의 '{key}'는 {TOOL_NAME}의 인자가 아닙니다")
    try:
        SearchRequest.model_validate(args)
    except ValidationError as exc:
        bad = ", ".join(str(err["loc"][0]) for err in exc.errors() if err["loc"])
        raise ValueError(f"사례 {i}: args 값이 올바르지 않습니다 ({bad})") from None


def load_cases(path: Path | str, expected_count: int | None = EXPECTED_CASES) -> list[Case]:
    """사례 파일을 읽고 형식을 검증한다. 문제가 있으면 ValueError."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("사례 파일은 목록(JSON 배열)이어야 합니다")
    cases: list[Case] = []
    for i, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"사례 {i}: 객체여야 합니다")
        utterance = item.get("utterance")
        if not isinstance(utterance, str) or not utterance.strip():
            raise ValueError(f"사례 {i}: utterance는 비어 있지 않은 문자열이어야 합니다")
        expect = item.get("expect")
        if not isinstance(expect, dict):
            raise ValueError(f"사례 {i}: expect 객체가 필요합니다")
        tool = expect.get("tool")
        if tool not in (TOOL_NAME, None):
            raise ValueError(f"사례 {i}: tool은 '{TOOL_NAME}' 또는 null이어야 합니다")
        args = expect.get("args", {})
        if not isinstance(args, dict):
            raise ValueError(f"사례 {i}: args는 객체여야 합니다")
        if tool is None and args:
            raise ValueError(f"사례 {i}: 도구를 부르지 않는 사례에는 args를 둘 수 없습니다")
        _validate_args(i, args)
        cases.append(Case(utterance=utterance, tool=tool, args=args))
    if expected_count is not None and len(cases) != expected_count:
        raise ValueError(f"사례는 {expected_count}개여야 합니다 (지금 {len(cases)}개)")
    return cases


# --- 채점 ---


def _norm(value: Any) -> str:
    """인자 비교용 문자열. enum은 값, 참/거짓은 소문자, 문자열은 앞뒤 공백 제거."""
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text.lower() if text.lower() in ("true", "false") else text


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _same(actual: Any, expected: Any) -> bool:
    """숫자끼리는 값으로 비교한다 (Gemini는 4를 4.0으로 준다). 그 밖에는 _norm 문자열 비교."""
    if _is_number(actual) and _is_number(expected):
        return actual == expected
    return _norm(actual) == _norm(expected)


def score(case: Case, resp: LLMResponse) -> tuple[bool, bool]:
    """(도구 선택 일치, 인자 부분 일치). 도구를 기대하지 않으면 호출이 없어야 통과."""
    if case.tool is None:
        ok = not resp.tool_calls
        return ok, ok
    call = next((tc for tc in resp.tool_calls if tc.name == case.tool), None)
    if call is None:
        return False, False
    args_ok = all(
        key in call.arguments and _same(call.arguments[key], expected)
        for key, expected in case.args.items()
    )
    return True, args_ok


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = PRICES_PER_1M.get(model)
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


# --- 결과와 보고서 ---


@dataclass
class CaseResult:
    tool_ok: bool
    args_ok: bool
    latency_s: float
    called: str  # 보고서용 요약: 부른 도구와 인자, 또는 "(답변)" / 오류 종류
    error: str | None


@dataclass
class ModelResult:
    label: str
    model: str
    results: list[CaseResult] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    usage_missing: int = 0

    def add(
        self,
        case: Case,
        resp: LLMResponse | None,
        *,
        latency_s: float,
        usage: dict[str, int] | None,
        error: str | None,
    ) -> CaseResult:
        if resp is None:
            tool_ok, args_ok, called = False, False, f"오류: {error}"
        else:
            tool_ok, args_ok = score(case, resp)
            called = _describe(resp)
        if usage is None:
            if resp is not None:  # 오류 난 호출은 청구 여부를 알 수 없어 따로 세지 않는다
                self.usage_missing += 1
        else:
            self.input_tokens += usage["input_tokens"]
            self.output_tokens += usage["output_tokens"]
        result = CaseResult(tool_ok, args_ok, latency_s, called, error)
        self.results.append(result)
        return result


def _describe(resp: LLMResponse) -> str:
    if not resp.tool_calls:
        return "(도구 없이 답변)"
    return "; ".join(
        f"{tc.name} {json.dumps(tc.arguments, ensure_ascii=False, sort_keys=True)}"
        for tc in resp.tool_calls
    )


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _mark(ok: bool) -> str:
    return "O" if ok else "X"


def render_report(cases: list[Case], models: list[ModelResult]) -> str:
    lines = [
        "# 한국어 LLM 품질 비교",
        "",
        f"- 사례 {len(cases)}개, 시스템 프롬프트는 운영과 같은 오케스트레이터 `SYSTEM`, "
        f"도구는 `{TOOL_NAME}` 하나",
        "- 도구 선택: 기대한 도구를 불렀는지(도구가 없어야 하는 사례는 부르지 않았는지)",
        "- 인자 일치: 사례에 적은 인자가 모두 같은 값인지(부분 일치, 앞뒤 공백 무시)",
        "- 단가(1M 토큰당 입력/출력 USD): "
        + ", ".join(f"{m} ${p[0]:.2f}/${p[1]:.2f}" for m, p in PRICES_PER_1M.items()),
        "",
        "## 요약",
        "",
        "| 모델 | 도구 선택 | 인자 일치 | 오류 | 평균 지연(ms) "
        "| 입력 토큰 | 출력 토큰 | 추정 비용(USD) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for m in models:
        n = len(m.results)
        tool_ok = sum(r.tool_ok for r in m.results)
        args_ok = sum(r.args_ok for r in m.results)
        errors = sum(r.error is not None for r in m.results)
        avg_ms = round(sum(r.latency_s for r in m.results) / n * 1000) if n else 0
        cost = estimate_cost(m.model, m.input_tokens, m.output_tokens)
        cost_text = "단가 없음" if cost is None else f"${cost:.6f}"
        lines.append(
            f"| {m.label} ({m.model}) | {tool_ok}/{n} | {args_ok}/{n} | {errors} | {avg_ms} "
            f"| {m.input_tokens} | {m.output_tokens} | {cost_text} |"
        )
    missing = [m for m in models if m.usage_missing]
    if missing:
        lines.append("")
        lines.extend(
            f"- {m.label}: {m.usage_missing}건은 사용량 정보가 없어 토큰·비용 합계에서 빠졌습니다"
            for m in missing
        )

    lines += ["", "## 사례별 결과", ""]
    header = "| # | 발화 | 기대 |" + "".join(f" {m.label} |" for m in models)
    lines += [header, "| --- | --- | --- |" + " --- |" * len(models)]
    for i, case in enumerate(cases):
        expect = "도구 없음" if case.tool is None else json.dumps(case.args, ensure_ascii=False)
        cells = []
        for m in models:
            r = m.results[i]
            cells.append(f"{_mark(r.tool_ok)}{_mark(r.args_ok)} {_cell(r.called)}")
        lines.append(
            f"| {i + 1} | {_cell(case.utterance)} | {_cell(expect)} | " + " | ".join(cells) + " |"
        )
    lines += ["", "표의 `OO`/`OX`/`XX`는 (도구 선택, 인자 일치) 결과입니다.", ""]
    return "\n".join(lines)


# --- 실행 ---


class EvalSettings(BaseSettings):
    """이 스크립트가 읽는 값만 둔다 (서버 설정 전체를 요구하지 않게)."""

    model_config = SettingsConfigDict(env_prefix="DODEED_", env_file=".env", extra="ignore")

    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    openai_model: str = "gpt-6-luna"
    gemini_model: str = "gemini-3.5-flash-lite"

    @field_validator("openai_api_key", "gemini_api_key", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


def _tool_def() -> ToolDef:
    return ToolDef(
        SEARCH_RESTAURANTS.name,
        SEARCH_RESTAURANTS.description,
        SEARCH_RESTAURANTS.input_model.model_json_schema(),
    )


async def _run_model(
    label: str,
    model: str,
    inner: OpenAIChatClient | GeminiClient,
    llm: GuardedLLM,
    cases: list[Case],
) -> ModelResult:
    result = ModelResult(label=label, model=model)
    tools = [_tool_def()]
    for i, case in enumerate(cases, start=1):
        messages = [
            Message(role="system", content=SYSTEM),
            Message(role="user", content=case.utterance),
        ]
        start = time.perf_counter()
        try:
            resp: LLMResponse | None = await llm.complete(messages, tools=tools)
            error = None
        except Exception as exc:  # 사례 하나의 실패로 비교 전체를 멈추지 않는다
            resp, error = None, type(exc).__name__  # 예외 메시지는 쓰지 않는다
        latency = time.perf_counter() - start
        usage = inner.last_usage if resp is not None else None
        r = result.add(case, resp, latency_s=latency, usage=usage, error=error)
        print(f"[{label}] {i:2d}/{len(cases)} {_mark(r.tool_ok)}{_mark(r.args_ok)} {latency:.2f}s")
    return result


async def _run(cfg: EvalSettings, cases: list[Case]) -> list[ModelResult]:
    registry = SecretRegistry(secrets.token_bytes(32))
    for key in (cfg.openai_api_key, cfg.gemini_api_key):
        if key:
            registry.register(key)
    results: list[ModelResult] = []
    async with httpx.AsyncClient() as http:
        if cfg.openai_api_key:
            openai = OpenAIChatClient(http, cfg.openai_api_key, cfg.openai_model, TIMEOUT_S)
            guarded = GuardedLLM(openai, registry, "orchestrator")
            results.append(await _run_model("OpenAI", cfg.openai_model, openai, guarded, cases))
        if cfg.gemini_api_key:
            gemini = GeminiClient(http, cfg.gemini_api_key, cfg.gemini_model, TIMEOUT_S)
            guarded = GuardedLLM(gemini, registry, "orchestrator")
            results.append(await _run_model("Gemini", cfg.gemini_model, gemini, guarded, cases))
    return results


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="한국어 사례로 1차(OpenAI)와 백업(Gemini) 모델의 도구 선택·인자·지연·비용을 "
        "비교해 마크다운 보고서를 쓴다. 키: DODEED_OPENAI_API_KEY, DODEED_GEMINI_API_KEY "
        "(환경변수 또는 .env).",
    )
    parser.add_argument("--out", required=True, type=Path, help="보고서(마크다운)를 쓸 경로")
    parser.add_argument(
        "--cases", type=Path, default=DEFAULT_CASES, help="사례 파일 (기본: 스크립트 옆 JSON)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        cases = load_cases(args.cases)
    except (OSError, ValueError) as exc:
        print(f"사례 파일을 읽을 수 없습니다: {exc}", file=sys.stderr)
        return 2
    try:
        cfg = EvalSettings()
    except ValidationError:
        print("설정을 읽을 수 없습니다. DODEED_ 환경변수와 .env를 확인하세요.", file=sys.stderr)
        return 2
    if not cfg.openai_api_key and not cfg.gemini_api_key:
        print(
            "API 키가 없습니다. DODEED_OPENAI_API_KEY 또는 DODEED_GEMINI_API_KEY를 "
            "환경변수나 .env에 넣은 뒤 다시 실행하세요.",
            file=sys.stderr,
        )
        return 2
    for name, key in (("OpenAI", cfg.openai_api_key), ("Gemini", cfg.gemini_api_key)):
        if not key:
            print(f"{name} 키가 없어 {name} 모델은 건너뜁니다.", file=sys.stderr)

    results = asyncio.run(_run(cfg, cases))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_report(cases, results), encoding="utf-8")
    print(f"보고서를 썼습니다: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
