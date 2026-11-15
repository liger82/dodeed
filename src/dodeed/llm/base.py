from typing import Protocol

from dodeed.llm.types import LLMResponse, Message, ToolDef


class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse: ...
