"""OpenAI Chat Completions 클라이언트 (모든 요청에 store=false).

재시도하지 않는다. 전환할 오류는 LLMProviderError로 올려 백업 래퍼가 처리한다.
로그와 오류 메시지에는 상태 코드와 예외 종류 이름만 남긴다.
"""

import json
import logging
from typing import Any

import httpx

from dodeed.llm.errors import LLMProviderError, status_error, transport_error
from dodeed.llm.types import LLMResponse, Message, ToolCall, ToolDef

BASE_URL = "https://api.openai.com/v1/chat/completions"
PROVIDER = "openai"

logger = logging.getLogger(__name__)


def _message_to_wire(m: Message) -> dict[str, Any]:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in m.tool_calls
            ],
        }
    return {"role": m.role, "content": m.content}


def _tool_to_wire(t: ToolDef) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
    }


def _bad_output(status: int | None = 200) -> LLMProviderError:
    return LLMProviderError(PROVIDER, "bad_output", status)


def _parse_arguments(raw: Any) -> dict:
    if raw == "":
        return {}
    if not isinstance(raw, str):
        raise _bad_output()
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise _bad_output() from None
    if not isinstance(parsed, dict):
        raise _bad_output()
    return parsed


def _parse_response(data: Any) -> LLMResponse:
    if not isinstance(data, dict):
        raise _bad_output()
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise _bad_output()
    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    if finish_reason == "content_filter":
        return LLMResponse(text=None, tool_calls=[])
    if finish_reason == "length":
        # 출력 한도로 잘렸다: 응답은 그대로 쓰되 종료 이유만 남긴다 (본문은 남기지 않는다)
        logger.warning("openai finish length")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise _bad_output()

    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise _bad_output()

    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raise _bad_output()
    tool_calls: list[ToolCall] = []
    for rc in raw_calls:
        if not isinstance(rc, dict):
            raise _bad_output()
        fn = rc.get("function")
        call_id = rc.get("id")
        if not isinstance(fn, dict) or not isinstance(call_id, str):
            raise _bad_output()
        name = fn.get("name")
        if not isinstance(name, str):
            raise _bad_output()
        tool_calls.append(
            ToolCall(
                id=call_id,
                name=name,
                arguments=_parse_arguments(fn.get("arguments")),
                provider=PROVIDER,
            )
        )

    return LLMResponse(text=content or None, tool_calls=tool_calls)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _parse_usage(data: Any) -> dict[str, int] | None:
    """`usage.prompt_tokens/completion_tokens` → {"input_tokens", "output_tokens"}. 없으면 None.

    completion_tokens에는 추론 토큰이 이미 들어 있다.
    """
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None
    inp, out = _count(usage.get("prompt_tokens")), _count(usage.get("completion_tokens"))
    if inp is None or out is None:
        return None
    return {"input_tokens": inp, "output_tokens": out}


class OpenAIChatClient:
    """앱의 공유 httpx 클라이언트를 쓴다 (여기서 닫지 않는다)."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        model: str,
        timeout_s: float,
        reasoning_effort: str | None = None,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._reasoning_effort = reasoning_effort
        # 최근 호출 1건의 사용량. 동시에 여러 요청이 오면 신뢰할 수 없다 (평가 스크립트 전용).
        # 실패하거나 없으면 None
        self.last_usage: dict[str, int] | None = None

    def _build_body(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None,
        json_schema: dict | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "store": False,
            "messages": [_message_to_wire(m) for m in messages],
        }
        if tools:
            body["tools"] = [_tool_to_wire(t) for t in tools]
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": json_schema, "strict": False},
            }
        if tools:
            # gpt-6-luna는 chat completions에서 도구와 추론을 함께 받지 않는다
            body["reasoning_effort"] = "none"
        elif self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort
        return body

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        self.last_usage = None
        body = self._build_body(messages, tools, json_schema)
        try:
            resp = await self._http.post(
                BASE_URL,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout_s,
            )
        except httpx.TransportError as exc:
            raise transport_error(PROVIDER, exc) from None

        status = resp.status_code
        error = status_error(PROVIDER, status)
        if error is not None:
            raise error

        try:
            data = resp.json()
        except ValueError:
            data = None
        try:
            result = _parse_response(data)
        except LLMProviderError:
            logger.warning("openai bad_output: status=%d", status)
            raise
        self.last_usage = _parse_usage(data)
        return result
