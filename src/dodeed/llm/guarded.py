import json

from dodeed.llm.base import LLMClient
from dodeed.llm.types import LLMResponse, Message, ToolDef
from dodeed.security.egress import SecretRegistry, check_egress

_ROLES = {"orchestrator", "reader"}  # 계획 3에서 "operator" 추가


def _dump(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


class GuardedLLM:
    """모든 요청과 응답을 출구 필터에 통과시키는 LLMClient 래퍼."""

    def __init__(self, inner: LLMClient, registry: SecretRegistry, role_name: str) -> None:
        if role_name not in _ROLES:
            raise ValueError(f"알 수 없는 역할: {role_name}")
        self.inner = inner
        self.registry = registry
        self.role_name = role_name

    def _check(self, text: str) -> None:
        check_egress(text, self.registry)

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        for m in messages:
            self._check(m.content)
            if m.tool_call_id is not None:
                self._check(m.tool_call_id)
            for tc in m.tool_calls or []:
                self._check(tc.name)
                self._check(_dump(tc.arguments))
        for t in tools or []:
            self._check(t.name)
            self._check(t.description)
            self._check(_dump(t.parameters))
        if json_schema is not None:
            self._check(_dump(json_schema))
        resp = await self.inner.complete(messages, tools, json_schema)
        if resp.text:
            self._check(resp.text)
        for tc in resp.tool_calls:
            self._check(tc.name)
            self._check(_dump(tc.arguments))
        return resp
