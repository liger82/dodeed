import logging
from pathlib import Path

import pytest
from pydantic import BaseModel

from dodeed.llm.fake import FakeLLM
from dodeed.llm.types import LLMResponse, ToolCall
from dodeed.reader.isolated import IsolatedReader
from dodeed.security.egress import EgressBlocked

FIXTURES = sorted((Path(__file__).parent.parent / "fixtures" / "injection").glob("*.txt"))


class Facts(BaseModel):
    inside: bool | None = None
    note: str | None = None


class Inner(BaseModel):
    label: str


class Nested(BaseModel):
    title: str
    tags: list[str]
    inner: Inner
    items: list[Inner]


def reader_with(responses, **kw):
    llm = FakeLLM(responses)
    return IsolatedReader(llm, **kw), llm


async def test_extract_valid_json():
    r, _ = reader_with([LLMResponse('{"inside": true, "note": null}', [])])
    assert await r.extract("후기", Facts, "추출") == Facts(inside=True, note=None)


async def test_tool_call_response_rejected():
    tc = ToolCall(id="1", name="reserve", arguments={})
    r, _ = reader_with([LLMResponse('{"inside": true}', [tc])])
    assert await r.extract("x", Facts, "추출") is None


@pytest.mark.parametrize("text", [None, "", "not json", "[1,2]", '{"inside": "maybe-not-bool"}'])
async def test_bad_responses_return_none(text):
    r, _ = reader_with([LLMResponse(text, [])])
    assert await r.extract("x", Facts, "추출") is None


async def test_long_input_truncated():
    r, llm = reader_with([LLMResponse("{}", [])])
    await r.extract("가" * 500_000, Facts, "추출")
    user = llm.calls[0].messages[-1]
    assert user.role == "user"
    assert len(user.content) <= 20_000 + 500


async def test_control_chars_removed_newline_kept():
    r, llm = reader_with([LLMResponse("{}", [])])
    await r.extract("a\x00b\x1bc\nd\te\x07", Facts, "추출")
    content = llm.calls[0].messages[-1].content
    assert "abc\nd\te" in content
    assert "\x00" not in content and "\x1b" not in content and "\x07" not in content


async def test_text_fields_truncated():
    r, _ = reader_with([LLMResponse('{"inside": true, "note": "' + "x" * 1000 + '"}', [])])
    res = await r.extract("x", Facts, "추출")
    assert res is not None and len(res.note) == 300


async def test_nested_fields_truncated():
    long = "y" * 1000
    body = (
        f'{{"title": "{long}", "tags": ["{long}"], "inner": {{"label": "{long}"}},'
        f' "items": [{{"label": "{long}"}}]}}'
    )
    r, _ = reader_with([LLMResponse(body, [])], max_text_field_chars=50)
    res = await r.extract("x", Nested, "추출")
    assert res is not None
    assert len(res.title) == 50 and len(res.tags[0]) == 50
    assert len(res.inner.label) == 50 and len(res.items[0].label) == 50


async def test_reader_never_receives_tools_and_gets_schema():
    r, llm = reader_with([LLMResponse("{}", [])])
    await r.extract("x", Facts, "추출")
    assert llm.calls[0].tools is None
    assert llm.calls[0].json_schema == Facts.model_json_schema()


async def test_message_layout_and_random_nonce():
    r, llm = reader_with([LLMResponse("{}", []), LLMResponse("{}", [])])
    await r.extract("원문1", Facts, "영업시간을 추출")
    await r.extract("원문1", Facts, "영업시간을 추출")
    m = llm.calls[0].messages
    assert [x.role for x in m] == ["system", "user"]
    assert "지시" in m[0].content
    assert m[1].content.startswith("영업시간을 추출")
    assert "<<<DATA-" in m[1].content and "<<<END-DATA-" in m[1].content
    assert m[1].content.index("원문1") > m[1].content.index("<<<DATA-")
    assert m[1].content != llm.calls[1].messages[1].content  # 호출마다 nonce 다름


async def test_inner_exception_returns_none_and_logs_without_content(caplog):
    class Boom:
        async def complete(self, *a, **k):
            raise RuntimeError("down")

    r = IsolatedReader(Boom())
    with caplog.at_level(logging.WARNING):
        assert await r.extract("비밀원문", Facts, "추출") is None
    assert "비밀원문" not in caplog.text


async def test_egress_blocked_propagates():
    class Blocked:
        async def complete(self, *a, **k):
            raise EgressBlocked("blocked")

    r = IsolatedReader(Blocked())
    with pytest.raises(EgressBlocked):
        await r.extract("x", Facts, "추출")


def test_enough_injection_fixtures():
    assert len(FIXTURES) >= 10


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
async def test_injection_fixture_tool_call_yields_none(path):
    tc = ToolCall(id="1", name="make_reservation", arguments={"restaurant": "x"})
    r, llm = reader_with([LLMResponse('{"inside": true}', [tc])])
    assert await r.extract(path.read_text(encoding="utf-8"), Facts, "추출") is None
    assert llm.calls[0].tools is None
