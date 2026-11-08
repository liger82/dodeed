import json

import httpx
import pytest

from dodeed.llm.errors import LLMProviderError, LLMRequestError
from dodeed.llm.openai_chat import BASE_URL, OpenAIChatClient
from dodeed.llm.types import LLMResponse, Message, ToolCall, ToolDef

API_KEY = "sk-test-SECRET-1234567890"


def _ok(message: dict, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"index": 0, "message": message, "finish_reason": finish_reason}]},
    )


def _sent(route) -> dict:
    return json.loads(route.calls.last.request.content)


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def client(http) -> OpenAIChatClient:
    return OpenAIChatClient(http, api_key=API_KEY, model="gpt-6-luna", timeout_s=60.0)


async def test_request_shape_has_store_false_and_tools(respx_mock, client):
    route = respx_mock.post(BASE_URL).mock(
        return_value=_ok({"role": "assistant", "content": "안녕"})
    )
    tool = ToolDef(
        name="search",
        description="맛집 검색",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    messages = [
        Message(role="system", content="규칙 1"),
        Message(role="system", content="규칙 2"),
        Message(role="user", content="점심 추천"),
    ]
    await client.complete(messages, tools=[tool])

    req = route.calls.last.request
    assert req.headers["Authorization"] == f"Bearer {API_KEY}"
    body = _sent(route)
    assert body["model"] == "gpt-6-luna"
    assert body["store"] is False
    assert body["messages"] == [
        {"role": "system", "content": "규칙 1"},
        {"role": "system", "content": "규칙 2"},
        {"role": "user", "content": "점심 추천"},
    ]
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "맛집 검색",
                "parameters": tool.parameters,
            },
        }
    ]
    assert "response_format" not in body
    assert "reasoning_effort" not in body


async def test_assistant_tool_call_and_tool_result_round_trip(respx_mock, client):
    route = respx_mock.post(BASE_URL).mock(
        return_value=_ok({"role": "assistant", "content": "결과예요"})
    )
    messages = [
        Message(role="user", content="강남 맛집"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="call_1", name="search", arguments={"q": "강남"})],
        ),
        Message(role="tool", content='{"items": []}', tool_call_id="call_1"),
    ]
    resp = await client.complete(messages)

    assert resp == LLMResponse(text="결과예요", tool_calls=[])
    sent = _sent(route)["messages"]
    assert sent[1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "search",
                    "arguments": json.dumps({"q": "강남"}, ensure_ascii=False),
                },
            }
        ],
    }
    assert "강남" in sent[1]["tool_calls"][0]["function"]["arguments"]
    assert sent[2] == {"role": "tool", "tool_call_id": "call_1", "content": '{"items": []}'}


async def test_assistant_without_tool_calls_is_plain_message(respx_mock, client):
    route = respx_mock.post(BASE_URL).mock(return_value=_ok({"content": "ok"}))
    await client.complete([Message(role="assistant", content="이전 답")])
    assert _sent(route)["messages"] == [{"role": "assistant", "content": "이전 답"}]


async def test_json_schema_sent_as_response_format(respx_mock, client):
    route = respx_mock.post(BASE_URL).mock(return_value=_ok({"content": '{"a": 1}'}))
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    resp = await client.complete([Message(role="user", content="x")], json_schema=schema)

    assert _sent(route)["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "output", "schema": schema, "strict": False},
    }
    assert resp.text == '{"a": 1}'


async def test_parses_text_and_tool_calls(respx_mock, client):
    respx_mock.post(BASE_URL).mock(
        return_value=_ok(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_a",
                        "type": "function",
                        "function": {"name": "search", "arguments": '{"q": "역삼"}'},
                    },
                    {
                        "id": "call_b",
                        "type": "function",
                        "function": {"name": "now", "arguments": ""},
                    },
                ],
            },
            finish_reason="tool_calls",
        )
    )
    resp = await client.complete([Message(role="user", content="x")])
    assert resp.text is None
    assert resp.tool_calls == [
        ToolCall(id="call_a", name="search", arguments={"q": "역삼"}, provider="openai"),
        ToolCall(id="call_b", name="now", arguments={}, provider="openai"),
    ]
    assert all(tc.thought_signature is None for tc in resp.tool_calls)


async def test_parses_plain_text(respx_mock, client):
    respx_mock.post(BASE_URL).mock(return_value=_ok({"role": "assistant", "content": "안녕"}))
    resp = await client.complete([Message(role="user", content="x")])
    assert resp == LLMResponse(text="안녕", tool_calls=[])


@pytest.mark.parametrize("arguments", ["not json", "[1, 2]", '"str"', "null"])
async def test_invalid_tool_arguments_raise_provider_error(respx_mock, client, arguments):
    respx_mock.post(BASE_URL).mock(
        return_value=_ok(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "c",
                        "type": "function",
                        "function": {"name": "search", "arguments": arguments},
                    }
                ],
            }
        )
    )
    with pytest.raises(LLMProviderError) as ei:
        await client.complete([Message(role="user", content="x")])
    assert ei.value.kind == "bad_output"
    assert ei.value.provider == "openai"


async def test_content_filter_returns_empty(respx_mock, client):
    respx_mock.post(BASE_URL).mock(
        return_value=_ok({"content": "부분 출력"}, finish_reason="content_filter")
    )
    resp = await client.complete([Message(role="user", content="x")])
    assert resp == LLMResponse(text=None, tool_calls=[])


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": "nope"},
        {"choices": [{"finish_reason": "stop"}]},
        {"choices": [{"message": "str"}]},
        {"choices": [{"message": {"content": 5}}]},
        {"choices": [{"message": {"tool_calls": [{"id": "c"}]}}]},
        [1, 2],
    ],
)
async def test_malformed_response_raises_bad_output(respx_mock, client, payload):
    respx_mock.post(BASE_URL).mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(LLMProviderError) as ei:
        await client.complete([Message(role="user", content="x")])
    assert ei.value.kind == "bad_output"


async def test_non_json_200_raises_bad_output(respx_mock, client):
    respx_mock.post(BASE_URL).mock(return_value=httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(LLMProviderError) as ei:
        await client.complete([Message(role="user", content="x")])
    assert ei.value.kind == "bad_output"
    assert ei.value.status == 200


@pytest.mark.parametrize(
    "status,kind", [(429, "rate_limited"), (500, "server_error"), (503, "server_error")]
)
async def test_retryable_status_raises_provider_error(respx_mock, client, status, kind):
    respx_mock.post(BASE_URL).mock(return_value=httpx.Response(status, json={"error": {}}))
    with pytest.raises(LLMProviderError) as ei:
        await client.complete([Message(role="user", content="x")])
    assert ei.value.kind == kind
    assert ei.value.status == status
    assert ei.value.provider == "openai"
    assert str(ei.value) == f"openai {kind}"


async def test_400_raises_request_error(respx_mock, client):
    respx_mock.post(BASE_URL).mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad param"}})
    )
    with pytest.raises(LLMRequestError) as ei:
        await client.complete([Message(role="user", content="x")])
    assert ei.value.status == 400
    assert ei.value.provider == "openai"
    assert str(ei.value) == "openai 400"
    assert not isinstance(ei.value, LLMProviderError)


async def test_timeout_and_connection_errors(respx_mock, client):
    route = respx_mock.post(BASE_URL)

    route.mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(LLMProviderError) as ei:
        await client.complete([Message(role="user", content="x")])
    assert ei.value.kind == "timeout"
    assert ei.value.status is None

    route.mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LLMProviderError) as ei:
        await client.complete([Message(role="user", content="x")])
    assert ei.value.kind == "connection"
    assert ei.value.status is None


async def test_timeout_passed_per_request(respx_mock, http):
    route = respx_mock.post(BASE_URL).mock(return_value=_ok({"content": "ok"}))
    c = OpenAIChatClient(http, api_key=API_KEY, model="gpt-6-luna", timeout_s=12.5)
    await c.complete([Message(role="user", content="x")])
    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout == {"connect": 12.5, "read": 12.5, "write": 12.5, "pool": 12.5}


async def test_errors_do_not_contain_api_key(respx_mock, client):
    route = respx_mock.post(BASE_URL)
    cases = [
        httpx.Response(401, json={"error": {"message": f"Incorrect API key {API_KEY}"}}),
        httpx.Response(500, text=f"boom {API_KEY}"),
        httpx.ConnectError(f"refused {API_KEY}"),
        httpx.Response(200, text=f"not json {API_KEY}"),
    ]
    for case in cases:
        if isinstance(case, Exception):
            route.mock(side_effect=case)
        else:
            route.mock(return_value=case)
        with pytest.raises((LLMProviderError, LLMRequestError)) as ei:
            await client.complete([Message(role="user", content="x")])
        assert API_KEY not in str(ei.value)
        assert API_KEY not in repr(ei.value)
        assert ei.value.__cause__ is None
        # 원래 예외(메시지에 키가 있을 수 있음)가 트레이스백에 따라 나오지 않아야 한다
        assert ei.value.__context__ is None or ei.value.__suppress_context__


async def test_reasoning_effort_only_when_set(respx_mock, http):
    route = respx_mock.post(BASE_URL).mock(return_value=_ok({"content": "ok"}))
    plain = OpenAIChatClient(http, api_key=API_KEY, model="gpt-6-luna", timeout_s=60.0)
    await plain.complete([Message(role="user", content="x")])
    assert "reasoning_effort" not in _sent(route)

    tuned = OpenAIChatClient(
        http, api_key=API_KEY, model="gpt-6-luna", timeout_s=60.0, reasoning_effort="low"
    )
    await tuned.complete([Message(role="user", content="x")])
    assert _sent(route)["reasoning_effort"] == "low"
