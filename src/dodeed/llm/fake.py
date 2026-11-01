from dataclasses import dataclass

from dodeed.llm.types import LLMResponse, Message, ToolDef


@dataclass
class RecordedCall:
    messages: list[Message]
    tools: list[ToolDef] | None
    json_schema: dict | None


class FakeLLM:
    """미리 넣어둔 응답을 순서대로 돌려주고 받은 요청을 기록한다."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[RecordedCall] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        self.calls.append(RecordedCall(messages, tools, json_schema))
        if not self._responses:
            raise AssertionError("FakeLLM: 준비된 응답이 없습니다")
        return self._responses.pop(0)
