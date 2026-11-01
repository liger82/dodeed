"""텔레그램 웹훅 어댑터 (REQ-CHN-001)."""

import hmac
from html import escape
from typing import Any

from aiogram import Bot
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from dodeed.channels.deps import get_gateway, get_telegram_bot
from dodeed.channels.gateway import Gateway
from dodeed.channels.messages import (
    Button,
    Inbound,
    InboundButton,
    InboundLocation,
    InboundText,
    OutApprovalRequest,
    Outbound,
    OutButtons,
    OutCardList,
    OutLocationRequest,
    OutText,
)
from dodeed.db import get_db

CHANNEL = "telegram"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
CALLBACK_DATA_MAX_BYTES = 64

router = APIRouter()


def to_inbound(update: dict[str, Any]) -> Inbound | None:
    event_id = str(update["update_id"])
    message = update.get("message")
    if message is not None:
        sender = message.get("from")
        if not sender:
            return None
        ext = str(sender["id"])
        if message.get("text") is not None:
            return InboundText(
                channel=CHANNEL, external_id=ext, text=message["text"], event_id=event_id
            )
        loc = message.get("location")
        if loc is not None:
            return InboundLocation(
                channel=CHANNEL,
                external_id=ext,
                lat=loc["latitude"],
                lng=loc["longitude"],
                event_id=event_id,
            )
        return None
    cq = update.get("callback_query")
    if cq is not None and cq.get("data") is not None and cq.get("from"):
        return InboundButton(
            channel=CHANNEL,
            external_id=str(cq["from"]["id"]),
            payload=cq["data"],
            event_id=event_id,
        )
    return None


def _chat_id(update: dict[str, Any]) -> int | None:
    message = update.get("message") or (update.get("callback_query") or {}).get("message")
    if not message:
        return None
    return message.get("chat", {}).get("id")


def _inline_button(b: Button) -> InlineKeyboardButton:
    if b.payload.startswith("http"):
        return InlineKeyboardButton(text=b.label, url=b.payload)
    if len(b.payload.encode()) > CALLBACK_DATA_MAX_BYTES:
        raise ValueError(f"callback_data는 {CALLBACK_DATA_MAX_BYTES}바이트 이하여야 합니다")
    return InlineKeyboardButton(text=b.label, callback_data=b.payload)


def _keyboard(buttons: list[Button]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[_inline_button(b)] for b in buttons])


async def send(bot: Bot, chat_id: int, out: Outbound) -> None:
    if isinstance(out, OutText):
        await bot.send_message(chat_id, out.text)
    elif isinstance(out, OutButtons):
        await bot.send_message(chat_id, out.text, reply_markup=_keyboard(out.buttons))
    elif isinstance(out, OutCardList):
        # 한 카드의 검증 실패가 일부만 전송된 상태를 만들지 않도록 먼저 모두 만든다
        prepared = [
            (
                "\n".join([f"<b>{escape(c.title)}</b>", *(escape(line) for line in c.lines)]),
                _keyboard(c.buttons),
            )
            for c in out.cards
        ]
        for text, markup in prepared:
            await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
    elif isinstance(out, OutLocationRequest):
        markup = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="현재 위치 보내기", request_location=True)]],
            one_time_keyboard=True,
            resize_keyboard=True,
        )
        await bot.send_message(chat_id, out.text, reply_markup=markup)
    elif isinstance(out, OutApprovalRequest):
        raise NotImplementedError("승인 요청은 계획 3에서 구현한다")
    else:  # pragma: no cover
        raise TypeError(type(out))


@router.post("/telegram/webhook")
async def webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    gateway: Gateway = Depends(get_gateway),
    bot: Bot = Depends(get_telegram_bot),
) -> dict[str, bool]:
    secret = request.app.state.settings.telegram_webhook_secret
    given = request.headers.get(SECRET_HEADER, "")
    if not hmac.compare_digest(given.encode(), secret.encode()):
        raise HTTPException(403, "forbidden")
    update = await request.json()
    msg = to_inbound(update)
    chat_id = _chat_id(update)
    if msg is None or chat_id is None:
        return {"ok": True}
    cq = update.get("callback_query")
    if cq is not None:
        await bot.answer_callback_query(cq["id"])
    for out in await gateway.handle(db, msg):
        await send(bot, chat_id, out)
    return {"ok": True}
