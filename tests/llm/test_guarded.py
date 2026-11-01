import pytest

from dodeed.llm.fake import FakeLLM
from dodeed.llm.guarded import GuardedLLM
from dodeed.llm.types import LLMResponse, Message, ToolCall, ToolDef
from dodeed.security.egress import EgressBlocked

SECRET = "tg-token-1234567890"


async def test_guarded_blocks_secret_in_request(registry):
    registry.register(SECRET)
    llm = GuardedLLM(FakeLLM([LLMResponse("ok", [])]), registry, "orchestrator")
    with pytest.raises(EgressBlocked):
        await llm.complete([Message("user", f"토큰 {SECRET}")])
    assert llm.inner.calls == []


async def test_guarded_blocks_secret_in_tool_def(registry):
    registry.register(SECRET)
    llm = GuardedLLM(FakeLLM([LLMResponse("ok", [])]), registry, "reader")
    tool = ToolDef("t", "설명", {"type": "object", "default": SECRET})
    with pytest.raises(EgressBlocked):
        await llm.complete([Message("user", "안녕")], tools=[tool])
    assert llm.inner.calls == []


async def test_guarded_blocks_secret_in_json_schema(registry):
    registry.register(SECRET)
    llm = GuardedLLM(FakeLLM([LLMResponse("ok", [])]), registry, "reader")
    with pytest.raises(EgressBlocked):
        await llm.complete([Message("user", "안녕")], json_schema={"const": SECRET})
    assert llm.inner.calls == []


async def test_guarded_blocks_secret_in_response(registry):
    registry.register(SECRET)
    llm = GuardedLLM(FakeLLM([LLMResponse(f"값은 {SECRET}", [])]), registry, "orchestrator")
    with pytest.raises(EgressBlocked):
        await llm.complete([Message("user", "안녕")])


async def test_guarded_blocks_secret_in_response_tool_call(registry):
    registry.register(SECRET)
    resp = LLMResponse(None, [ToolCall("1", "send", {"body": SECRET})])
    llm = GuardedLLM(FakeLLM([resp]), registry, "orchestrator")
    with pytest.raises(EgressBlocked):
        await llm.complete([Message("user", "안녕")])


async def test_guarded_passes_clean_request(registry):
    registry.register(SECRET)
    resp = LLMResponse("안녕하세요", [ToolCall("1", "search", {"q": "맛집"})])
    llm = GuardedLLM(FakeLLM([resp]), registry, "orchestrator")
    tool = ToolDef("search", "검색", {"type": "object"})
    out = await llm.complete([Message("user", "맛집 알려줘")], tools=[tool])
    assert out is resp
    assert len(llm.inner.calls) == 1
    assert llm.inner.calls[0].tools == [tool]


def test_guarded_rejects_unknown_role(registry):
    with pytest.raises(ValueError):
        GuardedLLM(FakeLLM([]), registry, "operator")
    assert GuardedLLM(FakeLLM([]), registry, "reader").role_name == "reader"


async def test_fake_llm_runs_out(registry):
    fake = FakeLLM([])
    with pytest.raises(AssertionError):
        await fake.complete([Message("user", "x")])


@pytest.mark.parametrize(
    "message",
    [
        Message("assistant", "", tool_calls=[ToolCall("1", "send", {"body": SECRET})]),
        Message("assistant", "", tool_calls=[ToolCall("1", f"x{SECRET}", {})]),
        Message("tool", "{}", tool_call_id=SECRET),
    ],
)
async def test_guarded_blocks_secret_in_message_tool_calls(registry, message):
    registry.register(SECRET)
    llm = GuardedLLM(FakeLLM([LLMResponse("ok", [])]), registry, "orchestrator")
    with pytest.raises(EgressBlocked):
        await llm.complete([Message("user", "안녕"), message])
    assert llm.inner.calls == []


def test_message_tool_calls_default_none():
    assert Message("user", "x").tool_calls is None
