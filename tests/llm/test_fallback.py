import logging
import uuid

import httpx
import pytest

from dodeed.app import create_app
from dodeed.db import get_db
from dodeed.llm.errors import LLMProviderError, LLMRequestError
from dodeed.llm.fake import FakeLLM
from dodeed.llm.fallback import FallbackLLM
from dodeed.llm.gemini import GeminiClient
from dodeed.llm.guarded import GuardedLLM
from dodeed.llm.openai_chat import BASE_URL as OPENAI_URL
from dodeed.llm.openai_chat import OpenAIChatClient
from dodeed.llm.types import LLMResponse, Message, ToolDef
from dodeed.security.egress import EgressBlocked
from tests.agent.helpers import audit_rows

GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)
OPENAI_KEY = "sk-test-SECRET-1234567890"
GEMINI_KEY = "AIza-test-SECRET-1234567890"
MSG_RETRY = "잠시 문제가 생겼어요. 잠시 후 다시 시도해 주세요."


class RaisingLLM:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    async def complete(self, messages, tools=None, json_schema=None) -> LLMResponse:
        self.calls += 1
        raise self.exc


def _args():
    messages = [Message(role="user", content="점심 추천")]
    tools = [ToolDef(name="search", description="검색", parameters={"type": "object"})]
    schema = {"type": "object"}
    return messages, tools, schema


async def test_primary_success_backup_not_called():
    primary = FakeLLM([LLMResponse(text="1차", tool_calls=[])])
    backup = FakeLLM([])
    llm = FallbackLLM(primary, backup, "openai", "gemini")
    resp = await llm.complete([Message(role="user", content="안녕")])
    assert resp.text == "1차"
    assert len(primary.calls) == 1
    assert backup.calls == []


@pytest.mark.parametrize(
    "kind", ["timeout", "connection", "rate_limited", "server_error", "bad_output"]
)
async def test_provider_error_falls_back(kind, caplog):
    primary = RaisingLLM(LLMProviderError("openai", kind))
    backup = FakeLLM([LLMResponse(text="백업", tool_calls=[])])
    llm = FallbackLLM(primary, backup, "openai", "gemini")
    messages, tools, schema = _args()
    with caplog.at_level(logging.WARNING, logger="dodeed.llm.fallback"):
        resp = await llm.complete(messages, tools, schema)

    assert resp.text == "백업"
    assert primary.calls == 1
    (call,) = backup.calls
    assert call.messages == messages and call.tools == tools and call.json_schema == schema
    (rec,) = [r for r in caplog.records if r.name == "dodeed.llm.fallback"]
    assert rec.getMessage() == f"llm fallback openai→gemini ({kind})"


async def test_request_error_does_not_fall_back():
    err = LLMRequestError("openai", 400)
    backup = FakeLLM([LLMResponse(text="백업", tool_calls=[])])
    llm = FallbackLLM(RaisingLLM(err), backup, "openai", "gemini")
    with pytest.raises(LLMRequestError) as info:
        await llm.complete([Message(role="user", content="안녕")])
    assert info.value is err
    assert backup.calls == []


@pytest.mark.parametrize("exc", [EgressBlocked("secret"), RuntimeError("boom")])
async def test_other_errors_do_not_fall_back(exc):
    backup = FakeLLM([LLMResponse(text="백업", tool_calls=[])])
    llm = FallbackLLM(RaisingLLM(exc), backup, "openai", "gemini")
    with pytest.raises(type(exc)) as info:
        await llm.complete([Message(role="user", content="안녕")])
    assert info.value is exc
    assert backup.calls == []


async def test_backup_error_propagates():
    backup_err = LLMProviderError("gemini", "server_error", 503)
    llm = FallbackLLM(
        RaisingLLM(LLMProviderError("openai", "timeout")),
        RaisingLLM(backup_err),
        "openai",
        "gemini",
    )
    with pytest.raises(LLMProviderError) as info:
        await llm.complete([Message(role="user", content="안녕")])
    assert info.value is backup_err


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def _real_fallback(http: httpx.AsyncClient) -> FallbackLLM:
    return FallbackLLM(
        OpenAIChatClient(http, api_key=OPENAI_KEY, model="gpt-6-luna", timeout_s=5.0),
        GeminiClient(http, api_key=GEMINI_KEY, model=GEMINI_MODEL, timeout_s=5.0),
        "openai",
        "gemini",
    )


async def test_invalid_primary_output_falls_back(respx_mock, http):
    broken = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "search", "arguments": "{"}}
        ],
    }
    openai = respx_mock.post(OPENAI_URL).respond(
        200, json={"choices": [{"index": 0, "message": broken, "finish_reason": "tool_calls"}]}
    )
    gemini = respx_mock.post(GEMINI_URL).respond(
        200,
        json={
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": "백업"}]}, "finishReason": "STOP"}
            ]
        },
    )
    resp = await _real_fallback(http).complete([Message(role="user", content="안녕")])
    assert resp.text == "백업"
    assert openai.call_count == 1
    assert gemini.call_count == 1


@pytest.mark.respx(assert_all_called=False)
async def test_egress_checked_once_before_any_provider(respx_mock, http, registry):
    secret = "tg-token-1234567890"
    registry.register(secret)
    openai = respx_mock.post(OPENAI_URL).respond(503)
    gemini = respx_mock.post(GEMINI_URL).respond(503)
    llm = GuardedLLM(_real_fallback(http), registry, "orchestrator")
    with pytest.raises(EgressBlocked):
        await llm.complete([Message(role="user", content=f"토큰 {secret}")])
    assert openai.call_count == 0
    assert gemini.call_count == 0


async def test_both_fail_user_gets_retry_message(
    respx_mock, settings, db_session, signed_up_user_id
):
    live = settings.model_copy(
        update={"llm": "live", "openai_api_key": OPENAI_KEY, "gemini_api_key": GEMINI_KEY}
    )
    openai = respx_mock.post(OPENAI_URL).respond(503)
    gemini = respx_mock.post(GEMINI_URL).respond(503)
    app = create_app(live)
    app.dependency_overrides[get_db] = lambda: db_session
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/api/login", json={"email": "svc@example.com", "password": "pw-12345678"}
            )
            assert r.status_code == 200
            r = await c.post("/api/chat", json={"text": "안녕", "event_id": str(uuid.uuid4())})

    assert r.status_code == 200
    assert r.json() == {"messages": [{"kind": "text", "text": MSG_RETRY}]}
    assert openai.call_count == 1
    assert gemini.call_count == 1
    rows = await audit_rows(db_session, signed_up_user_id)
    assert [row.decision for row in rows] == ["error"]
