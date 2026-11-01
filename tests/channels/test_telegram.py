import pytest

from dodeed.accounts.service import issue_link_code
from dodeed.channels.messages import (
    Button,
    Card,
    InboundButton,
    InboundLocation,
    InboundText,
    OutApprovalRequest,
    OutButtons,
    OutCardList,
    OutLocationRequest,
    OutText,
)
from dodeed.channels.telegram import send, to_inbound

HDR = {"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret"}


def tg_text(update_id, text, uid=42):
    return {
        "update_id": update_id,
        "message": {"message_id": 1, "from": {"id": uid}, "chat": {"id": uid}, "text": text},
    }


async def test_webhook_rejects_wrong_secret(channel_client):
    r = await channel_client.post("/telegram/webhook", json=tg_text(1, "hi"))
    assert r.status_code == 403
    r = await channel_client.post(
        "/telegram/webhook", json=tg_text(1, "hi"), headers={HDR_KEY: "bad"}
    )
    assert r.status_code == 403


HDR_KEY = "X-Telegram-Bot-Api-Secret-Token"


def test_to_inbound_text_location_callback():
    assert to_inbound(tg_text(7, "안녕")) == InboundText(
        channel="telegram", external_id="42", text="안녕", event_id="7"
    )
    loc = {
        "update_id": 8,
        "message": {
            "from": {"id": 42},
            "chat": {"id": 42},
            "location": {"latitude": 37.5, "longitude": 127.0},
        },
    }
    assert to_inbound(loc) == InboundLocation(
        channel="telegram", external_id="42", lat=37.5, lng=127.0, event_id="8"
    )
    cb = {
        "update_id": 9,
        "callback_query": {
            "id": "cq",
            "from": {"id": 42},
            "data": "pick:1",
            "message": {"chat": {"id": 99}},
        },
    }
    assert to_inbound(cb) == InboundButton(
        channel="telegram", external_id="42", payload="pick:1", event_id="9"
    )
    assert to_inbound({"update_id": 10, "edited_message": {}}) is None
    assert to_inbound({"update_id": 11, "message": {"from": {"id": 1}, "chat": {"id": 1}}}) is None


async def test_unsupported_update_returns_ok(channel_client, fake_bot):
    r = await channel_client.post(
        "/telegram/webhook", json={"update_id": 5, "edited_message": {}}, headers=HDR
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert fake_bot.calls == []


async def test_duplicate_update_processed_once(channel_client, fake_bot, orch, db_session):
    first = await channel_client.post("/telegram/webhook", json=tg_text(1, "hi"), headers=HDR)
    assert first.status_code == 200 and first.json() == {"ok": True}
    n = len(fake_bot.calls)
    assert n == 1  # 가입 안내
    again = await channel_client.post("/telegram/webhook", json=tg_text(1, "hi"), headers=HDR)
    assert again.status_code == 200
    assert len(fake_bot.calls) == n


async def test_linked_user_reply_goes_to_chat(
    channel_client, fake_bot, orch, db_session, signed_up_user_id, now
):
    code = await issue_link_code(db_session, signed_up_user_id, now)
    await channel_client.post("/telegram/webhook", json=tg_text(1, f"/link {code}"), headers=HDR)
    await channel_client.post("/telegram/webhook", json=tg_text(2, "맛집"), headers=HDR)
    assert fake_bot.calls[-1][1]["chat_id"] == 42
    assert fake_bot.calls[-1][1]["text"] == "echo:맛집"


async def test_callback_is_answered_and_replied_to_message_chat(
    channel_client, fake_bot, orch, db_session, signed_up_user_id, now
):
    code = await issue_link_code(db_session, signed_up_user_id, now)
    await channel_client.post("/telegram/webhook", json=tg_text(1, f"/link {code}"), headers=HDR)
    cb = {
        "update_id": 2,
        "callback_query": {
            "id": "cq1",
            "from": {"id": 42},
            "data": "pick:1",
            "message": {"chat": {"id": 99}},
        },
    }
    await channel_client.post("/telegram/webhook", json=cb, headers=HDR)
    names = [c[0] for c in fake_bot.calls]
    assert "answer_callback_query" in names
    ans = next(c for c in fake_bot.calls if c[0] == "answer_callback_query")
    assert ans[1]["callback_query_id"] == "cq1"
    assert fake_bot.calls[-1][1]["chat_id"] == 99


async def test_processing_error_propagates(channel_client, orch):
    async def boom(*a, **k):
        raise RuntimeError("x")

    orch.handle = boom
    channel_client._transport.app.state.gateway.handle = boom
    with pytest.raises(RuntimeError):
        await channel_client.post("/telegram/webhook", json=tg_text(3, "hi"), headers=HDR)


async def test_card_list_sent_as_messages_with_buttons(fake_bot):
    out = OutCardList(
        cards=[
            Card(
                title="<A&B>",
                lines=["l1", "<x>"],
                buttons=[
                    Button(label="선택", payload="pick:1"),
                    Button(label="지도", payload="https://m.e/1"),
                ],
            ),
            Card(title="둘째"),
        ]
    )
    await send(fake_bot, 5, out)
    assert len(fake_bot.calls) == 2
    c = fake_bot.calls[0][1]
    assert c["text"] == "<b>&lt;A&amp;B&gt;</b>\nl1\n&lt;x&gt;"
    assert c["parse_mode"] == "HTML"
    row = c["reply_markup"].inline_keyboard
    flat = [b for r in row for b in r]
    assert flat[0].callback_data == "pick:1" and flat[0].url is None
    assert flat[1].url == "https://m.e/1" and flat[1].callback_data is None
    assert fake_bot.calls[1][1]["reply_markup"] is None


async def test_text_buttons_location_and_approval(fake_bot):
    await send(fake_bot, 5, OutText(text="안녕"))
    assert fake_bot.calls[0][1]["text"] == "안녕"
    await send(fake_bot, 5, OutButtons(text="고르세요", buttons=[Button(label="네", payload="y")]))
    kb = fake_bot.calls[1][1]["reply_markup"].inline_keyboard
    assert kb[0][0].callback_data == "y"
    await send(fake_bot, 5, OutLocationRequest(text="위치?"))
    rk = fake_bot.calls[2][1]["reply_markup"]
    assert rk.one_time_keyboard is True
    assert rk.keyboard[0][0].request_location is True and len(rk.keyboard[0]) == 1
    with pytest.raises(NotImplementedError):
        await send(
            fake_bot,
            5,
            OutApprovalRequest(summary="s", lines=[], approve_payload="a", reject_payload="r"),
        )


async def test_callback_data_over_64_bytes_rejected(fake_bot):
    with pytest.raises(ValueError):
        await send(
            fake_bot, 5, OutButtons(text="t", buttons=[Button(label="x", payload="가" * 22)])
        )
    assert fake_bot.calls == []
