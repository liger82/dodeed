"""app.state에 주입된 채널 의존성을 꺼내는 FastAPI 의존성."""

from aiogram import Bot
from fastapi import HTTPException, Request

from dodeed.channels.gateway import Gateway


def get_gateway(request: Request) -> Gateway:
    gateway = getattr(request.app.state, "gateway", None)
    if gateway is None:
        raise HTTPException(503, "채널 게이트웨이가 준비되지 않았습니다")
    return gateway


def get_telegram_bot(request: Request) -> Bot:
    bot = getattr(request.app.state, "telegram_bot", None)
    if bot is None:
        raise HTTPException(503, "텔레그램 봇이 준비되지 않았습니다")
    return bot
