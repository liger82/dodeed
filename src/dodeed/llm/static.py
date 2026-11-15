"""실제 모델을 연결하기 전까지 쓰는 고정 응답 LLM (네트워크 없음)."""

from dodeed.llm.types import LLMResponse, Message, ToolDef

STATIC_TEXT = "아직 AI 모델이 연결되지 않았어요. 관리자에게 문의해 주세요."


class StaticLLM:
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        return LLMResponse(text=STATIC_TEXT, tool_calls=[])
