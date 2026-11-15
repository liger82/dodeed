import pytest

from dodeed.accounts.service import issue_link_code, resolve_user
from dodeed.channels.messages import (
    InboundButton,
    InboundLocation,
    InboundText,
    OutText,
)
from dodeed.sessions.service import get_or_create, get_state, set_state


def text(t, eid="e1", ext="tg-1"):
    return InboundText(channel="telegram", external_id=ext, text=t, event_id=eid)


async def test_unlinked_user_gets_signup_link_only(gateway, orch, db_session, now):
    out = await gateway.handle(db_session, text("안녕"), now=now)
    assert out == [
        OutText(
            text="dodeed를 쓰려면 먼저 가입해 주세요: http://test/signup\n"
            "가입 후 받은 연결 코드를 /link 123456 형식으로 보내 주세요."
        )
    ]
    assert orch.calls == []
    loc = InboundLocation(channel="telegram", external_id="tg-1", lat=1, lng=2, event_id="e2")
    btn = InboundButton(channel="telegram", external_id="tg-1", payload="x", event_id="e3")
    assert (await gateway.handle(db_session, loc, now=now))[0].text.startswith("dodeed를")
    assert (await gateway.handle(db_session, btn, now=now))[0].text.startswith("dodeed를")
    assert orch.calls == []


async def test_link_command_links_account(gateway, orch, db_session, signed_up_user_id, now):
    code = await issue_link_code(db_session, signed_up_user_id, now)
    out = await gateway.handle(db_session, text(f" /link {code} "), now=now)
    assert out == [OutText(text="연결되었어요. 이제 dodeed에게 말을 걸어 보세요.")]
    assert await resolve_user(db_session, "telegram", "tg-1") == signed_up_user_id
    out = await gateway.handle(db_session, text("맛집", eid="e2"), now=now)
    assert out == [OutText(text="echo:맛집")]
    assert orch.calls[0][0] == signed_up_user_id
    assert orch.calls[0][3].name == "서비스"


async def test_link_failure_message(gateway, db_session, now):
    out = await gateway.handle(db_session, text("/link 000000"), now=now)
    assert out == [
        OutText(
            text="연결 코드가 올바르지 않거나 만료되었어요. 앱이나 웹에서 새 코드를 받아 주세요."
        )
    ]


async def test_duplicate_event_processed_once(gateway, orch, db_session, signed_up_user_id, now):
    code = await issue_link_code(db_session, signed_up_user_id, now)
    await gateway.handle(db_session, text(f"/link {code}", eid="a"), now=now)
    first = await gateway.handle(db_session, text("hi", eid="b"), now=now)
    second = await gateway.handle(db_session, text("hi", eid="b"), now=now)
    assert first and second == []
    assert len(orch.calls) == 1


async def test_location_saved_to_state(gateway, orch, db_session, signed_up_user_id, now):
    code = await issue_link_code(db_session, signed_up_user_id, now)
    await gateway.handle(db_session, text(f"/link {code}", eid="a"), now=now)
    conv = await get_or_create(db_session, signed_up_user_id, "telegram")
    await set_state(db_session, conv.id, {"keep": 1})
    loc = InboundLocation(channel="telegram", external_id="tg-1", lat=37.5, lng=127.0, event_id="l")
    out = await gateway.handle(db_session, loc, now=now)
    assert out == [OutText(text="위치를 받았어요.")]
    state = await get_state(db_session, conv.id)
    assert state["keep"] == 1
    assert state["location"] == {"lat": 37.5, "lng": 127.0, "at": now.isoformat()}
    assert orch.calls == []


async def test_button_payload_goes_to_orchestrator(
    gateway, orch, db_session, signed_up_user_id, now
):
    code = await issue_link_code(db_session, signed_up_user_id, now)
    await gateway.handle(db_session, text(f"/link {code}", eid="a"), now=now)
    btn = InboundButton(channel="telegram", external_id="tg-1", payload="pick:1", event_id="b")
    await gateway.handle(db_session, btn, now=now)
    assert orch.calls[0][2] == "pick:1"


async def test_resolved_user_skips_link_lookup(gateway, orch, db_session, signed_up_user_id, now):
    msg = InboundText(channel="web", external_id="w", text="/link 123456", event_id="w1")
    out = await gateway.handle(db_session, msg, resolved_user_id=signed_up_user_id, now=now)
    assert out == [OutText(text="echo:/link 123456")]


async def test_link_bruteforce_lockout(gateway, db_session, signed_up_user_id, now):
    for i in range(5):
        out = await gateway.handle(db_session, text("/link 000000", eid=f"f{i}"), now=now)
        assert "올바르지 않거나" in out[0].text
    code = await issue_link_code(db_session, signed_up_user_id, now)
    out = await gateway.handle(db_session, text(f"/link {code}", eid="z"), now=now)
    assert out == [OutText(text="연결 시도가 너무 많아요. 10분 뒤에 다시 시도해 주세요.")]
    assert await resolve_user(db_session, "telegram", "tg-1") is None


async def test_successful_link_clears_counter(
    gateway, redis_client, db_session, signed_up_user_id, now
):
    await gateway.handle(db_session, text("/link 000000", eid="f"), now=now)
    assert await redis_client.get("dodeed:linkfail:telegram:tg-1") is not None
    code = await issue_link_code(db_session, signed_up_user_id, now)
    await gateway.handle(db_session, text(f"/link {code}", eid="ok"), now=now)
    assert await redis_client.get("dodeed:linkfail:telegram:tg-1") is None


async def test_failed_processing_releases_dedupe_key(
    gateway, orch, db_session, signed_up_user_id, now
):
    code = await issue_link_code(db_session, signed_up_user_id, now)
    await gateway.handle(db_session, text(f"/link {code}", eid="a"), now=now)
    original = orch.handle
    state = {"first": True}

    async def flaky(db, user_id, conversation, text_, pii):
        if state["first"]:
            state["first"] = False
            orch.calls.append("boom")
            raise RuntimeError("boom")
        return await original(db, user_id, conversation, text_, pii)

    orch.handle = flaky
    with pytest.raises(RuntimeError):
        await gateway.handle(db_session, text("hi", eid="r"), now=now)
    out = await gateway.handle(db_session, text("hi", eid="r"), now=now)
    assert out == [OutText(text="echo:hi")]
    assert len(orch.calls) == 2


async def test_orchestrator_llm_error_does_not_reach_gateway(
    settings, redis_client, db_session, signed_up_user_id, registry, now
):
    from dodeed.agent.orchestrator import MSG_RETRY_LATER, Orchestrator
    from dodeed.channels.gateway import Gateway
    from dodeed.llm.guarded import GuardedLLM
    from dodeed.policy.engine import PolicyEngine
    from dodeed.policy.tools import ToolRegistry

    class BrokenLLM:
        async def complete(self, messages, tools=None, json_schema=None):
            raise TimeoutError("provider timeout")

    orch = Orchestrator(
        GuardedLLM(BrokenLLM(), registry, "orchestrator"), ToolRegistry(), PolicyEngine()
    )
    gw = Gateway(orch, settings, redis_client)
    code = await issue_link_code(db_session, signed_up_user_id, now)
    await gw.handle(db_session, text(f"/link {code}"), now=now)
    out = await gw.handle(db_session, text("맛집", eid="e2"), now=now)
    assert out == [OutText(text=MSG_RETRY_LATER)]


async def test_link_fail_counter_always_has_ttl(gateway, redis_client, db_session, now):
    from dodeed.channels.gateway import LINK_FAIL_TTL

    key = "dodeed:linkfail:telegram:tg-1"
    await gateway.handle(db_session, text("/link 000000"), now=now)
    assert 0 < await redis_client.ttl(key) <= LINK_FAIL_TTL
    # 이전 처리가 INCR과 EXPIRE 사이에서 죽어 TTL 없이 남은 카운터도 다음 실패 때 TTL을 얻는다
    await redis_client.set(key, 2)
    assert await redis_client.ttl(key) == -1
    await gateway.handle(db_session, text("/link 000000", eid="e2"), now=now)
    assert int(await redis_client.get(key)) == 3
    assert 0 < await redis_client.ttl(key) <= LINK_FAIL_TTL
