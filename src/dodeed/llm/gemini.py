"""Gemini API generateContent 클라이언트 (백업 모델).

API 키는 URL 쿼리가 아니라 헤더 `x-goog-api-key`로만 보낸다. 재시도하지 않는다.
로그와 오류 메시지에는 상태 코드와 예외 종류 이름만 남긴다.

REST 필드 확인 (2026-11-08, Google 공식 문서 aistudio.google.com/api-reference 와
/docs; ai.google.dev 주소는 이곳으로 옮겨졌다):
- 엔드포인트 `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`,
  인증 헤더 `x-goog-api-key` (function-calling·structured-output 문서의 REST 예시).
- Content `{role: "user"|"model", parts}`, Part `text` / `functionCall {name, args, id}` /
  `functionResponse {name, response, id}`. Gemini 3는 functionCall마다 `id`를 준다고
  문서에 있으므로 응답의 id가 있으면 그대로 쓰고, 없을 때만 `gemini-<hex12>`를 만든다.
  (R6/R7 반영: 2026-11-08)
  functionResponse는 role "user"로 보낸다.
- Tool `functionDeclarations[{name, description, parametersJsonSchema}]`:
  `parametersJsonSchema`는 JSON Schema 객체를 그대로 받는다 (`parameters`와 함께 쓰지 않는다).
- 구조화 출력: SDK 예시는 `generationConfig.responseMimeType` + `responseJsonSchema`를 쓰고,
  현재 REST 예시는 같은 설정을 `generationConfig.responseFormat.text{mimeType, schema}`로
  보여 준다. 문서가 둘을 같은 설정으로 설명하므로 계획대로 `responseJsonSchema`를 쓴다.
- 응답 `candidates[0].content.parts`, `candidates[0].finishReason`, `promptFeedback.blockReason`.
- 사고 서명: Gemini 3는 현재 턴의 각 단계 첫 functionCall 파트에 `thoughtSignature`가
  없으면 400을 준다. 응답 파트의 `thoughtSignature`를 `ToolCall.thought_signature`에 담아
  다음 요청에서 그대로 돌려보낸다. 서명이 없는 호출(1차 OpenAI가 만든 호출 등)로 시작하는
  턴에는 문서가 API가 만들지 않은 함수 호출용으로 안내하는 `skip_thought_signature_validator`를
  첫 파트에만 넣는다 (문서는 품질이 떨어질 수 있다고 경고한다).
- 호출 id: `provider == "gemini"`인 호출만 functionCall/functionResponse에 `id`를 넣는다.
  다른 공급자의 id는 보내지 않고 이름으로 짝을 맞춘다.
"""

import json
import logging
from typing import Any
from uuid import uuid4

import httpx

from dodeed.llm.errors import LLMProviderError, LLMRequestError, status_error, transport_error
from dodeed.llm.types import LLMResponse, Message, ToolCall, ToolDef

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
PROVIDER = "gemini"
DUMMY_THOUGHT_SIGNATURE = "skip_thought_signature_validator"
BLOCKED_FINISH_REASONS = frozenset({"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"})

logger = logging.getLogger(__name__)


def _function_call_parts(calls: list[ToolCall]) -> list[dict[str, Any]]:
    """assistant 한 턴(단계)의 functionCall 파트들.

    Gemini가 만든 호출은 id와 실제 사고 서명을 그대로 돌려보낸다. 서명 없이 시작하는 턴
    (다른 공급자가 만든 호출)은 첫 파트에만 문서의 대체값을 넣는다 (병렬 호출은 첫 파트만 서명).
    """
    parts: list[dict[str, Any]] = []
    for i, tc in enumerate(calls):
        call: dict[str, Any] = {"name": tc.name, "args": tc.arguments}
        if tc.provider == PROVIDER:
            call["id"] = tc.id
        part: dict[str, Any] = {"functionCall": call}
        if tc.thought_signature:
            part["thoughtSignature"] = tc.thought_signature
        elif i == 0:
            part["thoughtSignature"] = DUMMY_THOUGHT_SIGNATURE
        parts.append(part)
    return parts


def _tool_response(content: str) -> dict:
    try:
        parsed = json.loads(content)
    except ValueError:
        parsed = None
    return parsed if isinstance(parsed, dict) else {"result": content}


def _to_contents(messages: list[Message]) -> tuple[list[str], list[dict[str, Any]]]:
    """(system 텍스트들, contents). 순서를 지키고 연속된 같은 role은 하나로 합친다.

    빈 텍스트(`""`)는 파트로 보내지 않고, 파트가 없는 content도 뺀다. 보낼 content가
    하나도 없으면 요청 전에 LLMRequestError(400)를 낸다.
    """
    system: list[str] = []
    contents: list[dict[str, Any]] = []
    calls_by_id: dict[str, ToolCall] = {}

    for m in messages:
        if m.role == "system":
            system.append(m.content)
            continue
        if m.role == "tool":
            call = calls_by_id.get(m.tool_call_id or "")
            if call is None:
                logger.warning("gemini request rejected: unknown tool_call_id")
                raise LLMRequestError(PROVIDER, 400)
            role = "user"
            response: dict[str, Any] = {"name": call.name, "response": _tool_response(m.content)}
            if call.provider == PROVIDER:
                response["id"] = call.id
            parts: list[dict[str, Any]] = [{"functionResponse": response}]
        elif m.role == "assistant" and m.tool_calls:
            role = "model"
            parts = [{"text": m.content}] if m.content else []
            parts += _function_call_parts(m.tool_calls)
            calls_by_id.update({tc.id: tc for tc in m.tool_calls})
        else:
            role = "model" if m.role == "assistant" else "user"
            parts = [{"text": m.content}] if m.content else []

        if not parts:  # 빈 텍스트 파트나 파트 없는 content는 보내지 않는다
            continue
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})
    if not contents:
        logger.warning("gemini request rejected: empty contents")
        raise LLMRequestError(PROVIDER, 400)
    return system, contents


def _tool_to_wire(t: ToolDef) -> dict[str, Any]:
    return {"name": t.name, "description": t.description, "parametersJsonSchema": t.parameters}


def _bad_output(status: int | None = 200) -> LLMProviderError:
    return LLMProviderError(PROVIDER, "bad_output", status)


def _parse_function_call(part: dict) -> ToolCall:
    fc = part["functionCall"]
    if not isinstance(fc, dict):
        raise _bad_output()
    name = fc.get("name")
    args = fc.get("args", {})
    if args is None:
        args = {}
    if not isinstance(name, str) or not isinstance(args, dict):
        raise _bad_output()
    call_id = fc.get("id")
    if not isinstance(call_id, str) or not call_id:
        call_id = f"gemini-{uuid4().hex[:12]}"
    signature = part.get("thoughtSignature")
    if not isinstance(signature, str) or not signature:
        signature = None
    return ToolCall(
        id=call_id, name=name, arguments=args, provider=PROVIDER, thought_signature=signature
    )


def _parse_response(data: Any) -> LLMResponse:
    if not isinstance(data, dict):
        raise _bad_output()
    feedback = data.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        return LLMResponse(text=None, tool_calls=[])

    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise _bad_output()
    candidate = candidates[0]
    if candidate.get("finishReason") in BLOCKED_FINISH_REASONS:
        return LLMResponse(text=None, tool_calls=[])

    content = candidate.get("content")
    if not isinstance(content, dict):
        raise _bad_output()
    parts = content.get("parts", [])
    if not isinstance(parts, list):
        raise _bad_output()

    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    for part in parts:
        if not isinstance(part, dict):
            raise _bad_output()
        if "functionCall" in part:
            tool_calls.append(_parse_function_call(part))
        elif "text" in part:
            text = part["text"]
            if not isinstance(text, str):
                raise _bad_output()
            if not part.get("thought"):  # 사고 요약은 답이 아니다
                texts.append(text)
    return LLMResponse(text="".join(texts) or None, tool_calls=tool_calls)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _parse_usage(data: Any) -> dict[str, int] | None:
    """`usageMetadata` → {"input_tokens", "output_tokens"}. 없으면 None.

    사고 토큰(`thoughtsTokenCount`)도 출력 단가로 청구되므로 출력에 더한다.
    출력이 없으면 `candidatesTokenCount`가 빠질 수 있어 0으로 본다.
    """
    meta = data.get("usageMetadata") if isinstance(data, dict) else None
    if not isinstance(meta, dict):
        return None
    inp = _count(meta.get("promptTokenCount"))
    out = _count(meta.get("candidatesTokenCount", 0))
    thoughts = _count(meta.get("thoughtsTokenCount", 0))
    if inp is None or out is None or thoughts is None:
        return None
    return {"input_tokens": inp, "output_tokens": out + thoughts}


class GeminiClient:
    """앱의 공유 httpx 클라이언트를 쓴다 (여기서 닫지 않는다)."""

    def __init__(self, http: httpx.AsyncClient, api_key: str, model: str, timeout_s: float) -> None:
        self._http = http
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        # 마지막 성공 호출의 토큰 사용량 (품질 비교 스크립트용). 실패하거나 없으면 None
        self.last_usage: dict[str, int] | None = None

    @property
    def _url(self) -> str:
        return f"{BASE_URL}/{self._model}:generateContent"

    def _build_body(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None,
        json_schema: dict | None,
    ) -> dict[str, Any]:
        system, contents = _to_contents(messages)
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": "\n".join(system)}]}
        if tools:
            body["tools"] = [{"functionDeclarations": [_tool_to_wire(t) for t in tools]}]
        if json_schema is not None:
            body["generationConfig"] = {
                "responseMimeType": "application/json",
                "responseJsonSchema": json_schema,
            }
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
                self._url,
                json=body,
                headers={"x-goog-api-key": self._api_key},
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
            logger.warning("gemini bad_output: status=%d", status)
            raise
        self.last_usage = _parse_usage(data)
        return result
