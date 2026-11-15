"""종단 테스트: 채널 → 게이트웨이 → 오케스트레이터 → 음식점 파이프라인 (외부 API는 respx)."""

import json

import pytest

from dodeed.llm.types import LLMResponse, Message, ToolDef
from tests.channels.conftest import FakeBot, redis_client  # noqa: F401
from tests.restaurant.conftest import http, scenario  # noqa: F401


@pytest.fixture
def fake_bot():
    return FakeBot()


class SchemaReaderLLM:
    """격리 리더용 대역: 스키마 이름을 보고 정해진 JSON을 돌려준다 (호출 순서와 무관)."""

    ANSWERS = {
        "HoursExtract": {"open_now": True, "closes_at": "22:00"},
        "ToiletExtract": {"gender_separated": True, "location": "inside"},
        "TasteExtract": {"summary": "국물이 진하고 깔끔해요"},
    }

    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        assert tools is None and json_schema is not None
        return LLMResponse(text=json.dumps(self.ANSWERS[json_schema["title"]]), tool_calls=[])


@pytest.fixture
def schema_reader_llm():
    return SchemaReaderLLM()
