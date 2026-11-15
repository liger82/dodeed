"""외부 원문 속 프롬프트 인젝션이 승인 안 된 도구 호출로 이어지지 않는다 (REQ-SEC-003/004)."""

import json
import unicodedata
from pathlib import Path

import pytest

from dodeed.app import build_orchestrator
from dodeed.llm.types import LLMResponse, Message, ToolCall, ToolDef
from dodeed.security.redaction import UserPII
from dodeed.sessions.service import get_or_create
from tests.agent.helpers import ScriptedLLM, audit_rows, dump_calls, text, tool_call
from tests.restaurant.conftest import PlaceFx, blog

FIXTURES = sorted((Path(__file__).resolve().parent.parent / "fixtures" / "injection").glob("*.txt"))
NO_PII = UserPII(name=None, phone=None, email=None, address=None)


def sentences(raw: str) -> list[str]:
    """픽스처에서 확인할 문장들: 제어 문자를 빼고 공백을 다듬은 줄 중 6글자 이상인 것.

    격리 리더가 제어 문자를 지우므로 지운 형태로 비교한다.
    """
    kept = "".join(ch for ch in raw if ch == "\n" or unicodedata.category(ch) != "Cc")
    return [line.strip() for line in kept.splitlines() if len(line.strip()) >= 6]


class ObeyingReaderLLM:
    """원문의 지시를 "따르는" 격리 리더 대역: 도구 호출, 스키마 밖 JSON, 원문 복사를 번갈아 낸다."""

    def __init__(self, injected: str) -> None:
        self.injected = injected
        self.calls: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        mode = len(self.calls) % 3
        if mode == 0:
            call = ToolCall(id="r", name="make_reservation", arguments={"party": 10})
            return LLMResponse(text=None, tool_calls=[call])
        if mode == 1:
            body = {
                "tool_calls": [{"name": "make_reservation", "arguments": {"restaurant": "A식당"}}],
                "admin": True,
                "instructions": self.injected,
            }
            return LLMResponse(text=json.dumps(body, ensure_ascii=False), tool_calls=[])
        first = sentences(self.injected)[0]
        body = {"summary": first, "closes_at": first, "location": first, "open_now": True}
        return LLMResponse(text=json.dumps(body, ensure_ascii=False), tool_calls=[])


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
async def test_injection_set_causes_no_unapproved_tool_call(
    fixture, settings, db_session, signed_up_user_id, http, scenario
):
    injected = fixture.read_text(encoding="utf-8")
    for i, name in enumerate(["가집", "나집", "다집"], start=1):
        posts = [
            blog(f"내돈내산 후기 {j}", injected, f"202610{10 + j:02d}", body=injected)
            for j in range(3)
        ]
        web = [{"title": "화장실", "link": f"https://example.org/{i}", "description": injected}]
        scenario.add(PlaceFx(str(i), name, i, blog=posts, web=web))

    reader_llm = ObeyingReaderLLM(injected)
    orch_llm = ScriptedLLM(
        [
            tool_call("search_restaurants", {"area_query": "강남역"}),
            # 오케스트레이터가 흔들려 쓰기 도구를 부르더라도 정책 엔진 밖으로 못 나간다
            tool_call("make_reservation", {"restaurant": "강남 황금포차"}, call_id="c2"),
            text("후보를 보여드릴게요."),
        ]
    )
    orch = build_orchestrator(
        settings,
        http=http,
        page_reader=scenario.page_reader,
        orchestrator_llm=orch_llm,
        reader_llm=reader_llm,
    )
    conv = await get_or_create(db_session, signed_up_user_id, "web")
    await orch.handle(db_session, signed_up_user_id, conv, "강남역 맛집", NO_PII)

    # 인젝션은 격리 리더까지는 갔다 (테스트가 의미 있는지 확인)
    reader_seen = "\n".join(m.content for call in reader_llm.calls for m in call)
    assert sentences(injected)[0] in reader_seen

    # 오케스트레이터 LLM은 픽스처 문장을 하나도 받지 않았다
    seen = dump_calls(orch_llm.calls)
    for sentence in sentences(injected):
        assert sentence not in seen, sentence

    # 정책 엔진이 허용한 호출은 search_restaurants뿐이다
    rows = await audit_rows(db_session, signed_up_user_id)
    allowed = [r.tool for r in rows if r.decision == "allow"]
    assert allowed == ["search_restaurants"]
