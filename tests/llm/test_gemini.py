import json
import re

import httpx
import pytest

from dodeed.llm.errors import LLMProviderError, LLMRequestError
from dodeed.llm.gemini import DUMMY_THOUGHT_SIGNATURE, GeminiClient
from dodeed.llm.types import LLMResponse, Message, ToolCall, ToolDef

API_KEY = "AIza-test-SECRET-1234567890"
MODEL = "gemini-3.5-flash-lite"
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def _ok(parts: list, finish_reason: str = "STOP", **extra) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {"content": {"role": "model", "parts": parts}, "finishReason": finish_reason}
            ],
            **extra,
        },
    )


def _sent(route) -> dict:
    return json.loads(route.calls.last.request.content)


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def client(http) -> GeminiClient:
    return GeminiClient(http, api_key=API_KEY, model=MODEL, timeout_s=60.0)


async def test_request_uses_header_key_not_query(respx_mock, client):
    route = respx_mock.post(URL).mock(return_value=_ok([{"text": "안녕"}]))
    resp = await client.complete([Message(role="user", content="x")])

    req = route.calls.last.request
    assert str(req.url) == URL
    assert "key=" not in str(req.url)
    assert API_KEY not in str(req.url)
    assert req.headers["x-goog-api-key"] == API_KEY
    assert "authorization" not in req.headers
    assert resp == LLMResponse(text="안녕", tool_calls=[])


async def test_system_and_roles_mapped(respx_mock, client):
    route = respx_mock.post(URL).mock(return_value=_ok([{"text": "ok"}]))
    messages = [
        Message(role="system", content="규칙 1"),
        Message(role="user", content="점심 추천"),
        Message(role="system", content="규칙 2"),
        Message(role="assistant", content="어디세요?"),
        Message(role="user", content="강남"),
        Message(role="user", content="혼밥"),
    ]
    await client.complete(messages)

    body = _sent(route)
    assert body["systemInstruction"] == {"parts": [{"text": "규칙 1\n규칙 2"}]}
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "점심 추천"}]},
        {"role": "model", "parts": [{"text": "어디세요?"}]},
        {"role": "user", "parts": [{"text": "강남"}, {"text": "혼밥"}]},
    ]
    assert "tools" not in body
    assert "generationConfig" not in body


async def test_no_system_instruction_when_no_system_message(respx_mock, client):
    route = respx_mock.post(URL).mock(return_value=_ok([{"text": "ok"}]))
    await client.complete([Message(role="user", content="x")])
    assert "systemInstruction" not in _sent(route)


async def test_tools_sent_as_function_declarations(respx_mock, client):
    route = respx_mock.post(URL).mock(return_value=_ok([{"text": "ok"}]))
    tool = ToolDef(
        name="search",
        description="맛집 검색",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    await client.complete([Message(role="user", content="x")], tools=[tool])
    assert _sent(route)["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "search",
                    "description": "맛집 검색",
                    "parametersJsonSchema": tool.parameters,
                }
            ]
        }
    ]


async def test_tool_result_mapped_to_function_name_by_call_id(respx_mock, client):
    route = respx_mock.post(URL).mock(return_value=_ok([{"text": "결과예요"}]))
    messages = [
        Message(role="user", content="강남 맛집이랑 지금 시각"),
        Message(
            role="assistant",
            content="찾아볼게요",
            tool_calls=[
                ToolCall(id="call_abc", name="search", arguments={"q": "강남"}),
                ToolCall(id="call_def", name="now", arguments={}),
            ],
        ),
        Message(role="tool", content='{"items": []}', tool_call_id="call_abc"),
        Message(role="tool", content="12:30", tool_call_id="call_def"),
    ]
    resp = await client.complete(messages)

    assert resp == LLMResponse(text="결과예요", tool_calls=[])
    contents = _sent(route)["contents"]
    assert contents[1] == {
        "role": "model",
        "parts": [
            {"text": "찾아볼게요"},
            {
                "functionCall": {"name": "search", "args": {"q": "강남"}},
                "thoughtSignature": DUMMY_THOUGHT_SIGNATURE,
            },
            {"functionCall": {"name": "now", "args": {}}},
        ],
    }
    # 연속된 tool 결과는 하나의 user content로 합친다
    assert contents[2] == {
        "role": "user",
        "parts": [
            {
                "functionResponse": {
                    "name": "search",
                    "response": {"items": []},
                }
            },
            {
                "functionResponse": {
                    "name": "now",
                    "response": {"result": "12:30"},
                }
            },
        ],
    }
    assert len(contents) == 3


@pytest.mark.parametrize("content", ["[1, 2]", '"str"', "null", "not json", ""])
async def test_non_object_tool_result_wrapped(respx_mock, client, content):
    route = respx_mock.post(URL).mock(return_value=_ok([{"text": "ok"}]))
    messages = [
        Message(role="user", content="x"),
        Message(role="assistant", content="", tool_calls=[ToolCall("c1", "f", {})]),
        Message(role="tool", content=content, tool_call_id="c1"),
    ]
    await client.complete(messages)
    contents = _sent(route)["contents"]
    # 내용 없는 assistant 턴에는 빈 텍스트 파트를 넣지 않는다
    assert contents[1]["parts"] == [
        {
            "functionCall": {"name": "f", "args": {}},
            "thoughtSignature": DUMMY_THOUGHT_SIGNATURE,
        }
    ]
    assert contents[2]["parts"][0]["functionResponse"]["response"] == {"result": content}


async def test_thought_signature_round_trips_to_next_request(respx_mock, client):
    route = respx_mock.post(URL)
    route.mock(
        return_value=_ok(
            [
                {
                    "functionCall": {"name": "search", "args": {"q": "역삼"}, "id": "g1"},
                    "thoughtSignature": "REAL-SIG-1",
                },
                {"functionCall": {"name": "now", "args": {}, "id": "g2"}},
            ]
        )
    )
    history = [Message(role="user", content="역삼 맛집, 지금 몇 시?")]
    first = await client.complete(history)

    history.append(Message(role="assistant", content="", tool_calls=list(first.tool_calls)))
    history.append(Message(role="tool", content='{"items": []}', tool_call_id="g1"))
    history.append(Message(role="tool", content="12:30", tool_call_id="g2"))
    route.mock(return_value=_ok([{"text": "끝"}]))
    await client.complete(history)

    contents = _sent(route)["contents"]
    assert contents[1] == {
        "role": "model",
        "parts": [
            {
                "functionCall": {"name": "search", "args": {"q": "역삼"}, "id": "g1"},
                "thoughtSignature": "REAL-SIG-1",
            },
            {"functionCall": {"name": "now", "args": {}, "id": "g2"}},
        ],
    }
    assert contents[2]["parts"] == [
        {"functionResponse": {"name": "search", "response": {"items": []}, "id": "g1"}},
        {"functionResponse": {"name": "now", "response": {"result": "12:30"}, "id": "g2"}},
    ]
    assert DUMMY_THOUGHT_SIGNATURE not in route.calls.last.request.content.decode()


async def test_mixed_history_openai_call_has_no_id_and_placeholder(respx_mock, client):
    route = respx_mock.post(URL).mock(return_value=_ok([{"text": "ok"}]))
    messages = [
        Message(role="user", content="강남 맛집"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(id="call_abc", name="search", arguments={"q": "강남"}, provider="openai")
            ],
        ),
        Message(role="tool", content='{"items": [1]}', tool_call_id="call_abc"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="g9",
                    name="now",
                    arguments={},
                    provider="gemini",
                    thought_signature="REAL-SIG-9",
                )
            ],
        ),
        Message(role="tool", content="12:30", tool_call_id="g9"),
    ]
    await client.complete(messages)

    contents = _sent(route)["contents"]
    # OpenAI가 만든 호출: id 없음, 대체 서명
    assert contents[1]["parts"] == [
        {
            "functionCall": {"name": "search", "args": {"q": "강남"}},
            "thoughtSignature": DUMMY_THOUGHT_SIGNATURE,
        }
    ]
    assert contents[2]["parts"] == [
        {"functionResponse": {"name": "search", "response": {"items": [1]}}}
    ]
    assert "call_abc" not in route.calls.last.request.content.decode()
    # Gemini가 만든 호출: id와 실제 서명을 그대로
    assert contents[3]["parts"] == [
        {
            "functionCall": {"name": "now", "args": {}, "id": "g9"},
            "thoughtSignature": "REAL-SIG-9",
        }
    ]
    assert contents[4]["parts"] == [
        {"functionResponse": {"name": "now", "response": {"result": "12:30"}, "id": "g9"}}
    ]


async def test_unknown_tool_call_id_raises_request_error(respx_mock, client):
    route = respx_mock.post(URL).mock(return_value=_ok([{"text": "ok"}]))
    messages = [
        Message(role="user", content="x"),
        Message(role="tool", content="{}", tool_call_id="call_missing"),
    ]
    with pytest.raises(LLMRequestError) as ei:
        await client.complete(messages)
    assert ei.value.provider == "gemini"
    assert not route.called


async def test_function_call_parsed_with_generated_id(respx_mock, client):
    respx_mock.post(URL).mock(
        return_value=_ok(
            [
                {"text": "찾아볼게요. "},
                {"functionCall": {"name": "search", "args": {"q": "역삼"}}},
                {"functionCall": {"name": "now"}},
                {"text": "잠시만요"},
            ]
        )
    )
    resp = await client.complete([Message(role="user", content="x")])
    assert resp.text == "찾아볼게요. 잠시만요"
    assert [(tc.name, tc.arguments) for tc in resp.tool_calls] == [
        ("search", {"q": "역삼"}),
        ("now", {}),
    ]
    assert all(tc.provider == "gemini" for tc in resp.tool_calls)
    assert all(tc.thought_signature is None for tc in resp.tool_calls)
    ids = [tc.id for tc in resp.tool_calls]
    assert all(re.fullmatch(r"gemini-[0-9a-f]{12}", i) for i in ids)
    assert len(set(ids)) == 2


async def test_function_call_id_from_response_is_kept(respx_mock, client):
    respx_mock.post(URL).mock(
        return_value=_ok(
            [
                {
                    "functionCall": {"name": "search", "args": {}, "id": "abc123"},
                    "thoughtSignature": "sig",
                }
            ]
        )
    )
    resp = await client.complete([Message(role="user", content="x")])
    assert resp == LLMResponse(
        text=None,
        tool_calls=[
            ToolCall(
                id="abc123",
                name="search",
                arguments={},
                provider="gemini",
                thought_signature="sig",
            )
        ],
    )


async def test_thought_parts_are_not_returned_as_text(respx_mock, client):
    respx_mock.post(URL).mock(
        return_value=_ok([{"text": "생각 요약", "thought": True}, {"text": "답"}])
    )
    resp = await client.complete([Message(role="user", content="x")])
    assert resp.text == "답"


async def test_json_schema_sent_as_response_json_schema(respx_mock, client):
    route = respx_mock.post(URL).mock(return_value=_ok([{"text": '{"a": 1}'}]))
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    resp = await client.complete([Message(role="user", content="x")], json_schema=schema)
    assert _sent(route)["generationConfig"] == {
        "responseMimeType": "application/json",
        "responseJsonSchema": schema,
    }
    assert resp.text == '{"a": 1}'


@pytest.mark.parametrize("reason", ["SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"])
async def test_safety_block_returns_empty(respx_mock, client, reason):
    respx_mock.post(URL).mock(return_value=_ok([{"text": "부분 출력"}], finish_reason=reason))
    resp = await client.complete([Message(role="user", content="x")])
    assert resp == LLMResponse(text=None, tool_calls=[])


async def test_prompt_block_returns_empty(respx_mock, client):
    respx_mock.post(URL).mock(
        return_value=httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
    )
    resp = await client.complete([Message(role="user", content="x")])
    assert resp == LLMResponse(text=None, tool_calls=[])


async def test_candidate_without_parts_returns_empty(respx_mock, client):
    respx_mock.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={"candidates": [{"content": {"role": "model"}, "finishReason": "MAX_TOKENS"}]},
        )
    )
    resp = await client.complete([Message(role="user", content="x")])
    assert resp == LLMResponse(text=None, tool_calls=[])


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"candidates": []},
        {"candidates": "nope"},
        {"candidates": ["str"]},
        {"candidates": [{"finishReason": "STOP"}]},
        {"candidates": [{"content": "str"}]},
        {"candidates": [{"content": {"parts": "str"}}]},
        {"candidates": [{"content": {"parts": ["str"]}}]},
        {"candidates": [{"content": {"parts": [{"text": 5}]}}]},
        {"candidates": [{"content": {"parts": [{"functionCall": "str"}]}}]},
        {"candidates": [{"content": {"parts": [{"functionCall": {"args": {}}}]}}]},
        {"candidates": [{"content": {"parts": [{"functionCall": {"name": "f", "args": [1]}}]}}]},
        {"candidates": [{"content": {"parts": [{"functionCall": {"name": "f", "args": "{}"}}]}}]},
        [1, 2],
    ],
)
async def test_malformed_response_raises_bad_output(respx_mock, client, payload):
    respx_mock.post(URL).mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(LLMProviderError) as ei:
        await client.complete([Message(role="user", content="x")])
    assert ei.value.kind == "bad_output"
    assert ei.value.provider == "gemini"


async def test_non_json_200_raises_bad_output(respx_mock, client):
    respx_mock.post(URL).mock(return_value=httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(LLMProviderError) as ei:
        await client.complete([Message(role="user", content="x")])
    assert ei.value.kind == "bad_output"
    assert ei.value.status == 200


async def test_retryable_and_request_errors(respx_mock, client):
    route = respx_mock.post(URL)
    for status, kind in [(429, "rate_limited"), (500, "server_error"), (503, "server_error")]:
        route.mock(return_value=httpx.Response(status, json={"error": {}}))
        with pytest.raises(LLMProviderError) as ei:
            await client.complete([Message(role="user", content="x")])
        assert (ei.value.provider, ei.value.kind, ei.value.status) == ("gemini", kind, status)
        assert str(ei.value) == f"gemini {kind}"

    for status in (400, 403, 404):
        route.mock(return_value=httpx.Response(status, json={"error": {"message": "bad"}}))
        with pytest.raises(LLMRequestError) as ei:
            await client.complete([Message(role="user", content="x")])
        assert (ei.value.provider, ei.value.status) == ("gemini", status)
        assert str(ei.value) == f"gemini {status}"
        assert not isinstance(ei.value, LLMProviderError)

    route.mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(LLMProviderError) as ei:
        await client.complete([Message(role="user", content="x")])
    assert (ei.value.kind, ei.value.status) == ("timeout", None)

    route.mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LLMProviderError) as ei:
        await client.complete([Message(role="user", content="x")])
    assert (ei.value.kind, ei.value.status) == ("connection", None)


async def test_timeout_passed_per_request(respx_mock, http):
    route = respx_mock.post(URL).mock(return_value=_ok([{"text": "ok"}]))
    c = GeminiClient(http, api_key=API_KEY, model=MODEL, timeout_s=12.5)
    await c.complete([Message(role="user", content="x")])
    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout == {"connect": 12.5, "read": 12.5, "write": 12.5, "pool": 12.5}


async def test_errors_do_not_contain_api_key(respx_mock, client, caplog):
    route = respx_mock.post(URL)
    cases = [
        httpx.Response(400, json={"error": {"message": f"API key not valid {API_KEY}"}}),
        httpx.Response(500, text=f"boom {API_KEY}"),
        httpx.ConnectError(f"refused {API_KEY}"),
        httpx.ReadTimeout(f"timeout {API_KEY}"),
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
        assert ei.value.__context__ is None or ei.value.__suppress_context__
    assert API_KEY not in caplog.text
