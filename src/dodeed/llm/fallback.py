"""1차 모델이 전환할 오류(LLMProviderError)를 내면 같은 요청을 백업 모델로 보낸다.

LLMRequestError, EgressBlocked 등 그 밖의 예외와 백업의 예외는 그대로 올린다.
출구 필터(GuardedLLM)는 이 래퍼 바깥에 두므로 요청은 한 번만 검사된다.
"""

import logging

from dodeed.llm.base import LLMClient
from dodeed.llm.errors import LLMProviderError
from dodeed.llm.types import LLMResponse, Message, ToolDef

logger = logging.getLogger(__name__)


class FallbackLLM:
    def __init__(
        self, primary: LLMClient, backup: LLMClient, primary_name: str, backup_name: str
    ) -> None:
        self.primary = primary
        self.backup = backup
        self.primary_name = primary_name
        self.backup_name = backup_name

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        try:
            return await self.primary.complete(messages, tools, json_schema)
        except LLMProviderError as exc:
            logger.warning("llm fallback %s→%s (%s)", self.primary_name, self.backup_name, exc.kind)
        return await self.backup.complete(messages, tools, json_schema)
