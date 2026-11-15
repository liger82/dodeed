"""오케스트레이터·종단 테스트에서 함께 쓰는 도우미."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dodeed.audit.models import AuditLog, AuditRef
from dodeed.llm.fake import RecordedCall
from dodeed.llm.types import LLMResponse, Message, ToolCall, ToolDef

Step = LLMResponse | Callable[[list[Message]], LLMResponse]


class ScriptedLLM:
    """순서대로 응답한다. 응답 자리에 함수를 두면 받은 메시지를 보고 응답을 만든다."""

    def __init__(self, steps: list[Step]) -> None:
        self._steps = list(steps)
        self.calls: list[RecordedCall] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        self.calls.append(RecordedCall(list(messages), tools, json_schema))
        if not self._steps:
            raise AssertionError("ScriptedLLM: 준비된 응답이 없습니다")
        step = self._steps.pop(0)
        return step(messages) if callable(step) else step


def tool_call(name: str, arguments: dict | None = None, call_id: str = "c1") -> LLMResponse:
    return LLMResponse(
        text=None, tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments or {})]
    )


def text(value: str) -> LLMResponse:
    return LLMResponse(text=value, tool_calls=[])


def dump_calls(calls: list[RecordedCall]) -> str:
    """LLM이 받은 모든 것(메시지 본문, 도구 호출, 도구 정의)을 한 문자열로 만든다."""
    parts = []
    for c in calls:
        for m in c.messages:
            parts.append(m.content)
            for tc in getattr(m, "tool_calls", None) or []:
                parts.append(tc.name)
                parts.append(json.dumps(tc.arguments, ensure_ascii=False))
    return "\n".join(parts)


def tool_results(calls: list[RecordedCall]) -> list[dict]:
    """마지막 호출에 담긴 도구 결과 메시지를 JSON으로 돌려준다."""
    return [json.loads(m.content) for m in calls[-1].messages if m.role == "tool"]


async def audit_rows(db: AsyncSession, user_id: uuid.UUID) -> list[AuditLog]:
    ref = await db.scalar(select(AuditRef.actor_ref).where(AuditRef.user_id == user_id))
    if ref is None:
        return []
    rows = await db.scalars(select(AuditLog).where(AuditLog.actor_ref == ref).order_by(AuditLog.id))
    return list(rows)
