import logging
import uuid

import httpx
import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from dodeed.app import create_app
from dodeed.channels.gateway import Gateway
from dodeed.llm.static import STATIC_TEXT
from dodeed.sources.public_page import PublicPageReader


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_default_wiring_builds_gateway_and_bot(settings):
    app = create_app(settings)
    assert isinstance(app.state.gateway, Gateway)
    assert isinstance(app.state.telegram_bot, Bot)
    # 요청 URL(API 키 포함)이 INFO 로그에 남지 않는다
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_llm_setting_defaults_to_fake(settings):
    assert settings.llm == "fake"


async def test_default_wiring_answers_with_static_llm(client, signed_up_user_id):
    await client.post("/api/login", json={"email": "svc@example.com", "password": "pw-12345678"})
    r = await client.post("/api/chat", json={"text": "안녕", "event_id": str(uuid.uuid4())})
    assert r.status_code == 200
    assert r.json() == {"messages": [{"kind": "text", "text": STATIC_TEXT}]}


async def test_lifespan_closes_owned_resources(settings, monkeypatch):
    closed: list[str] = []

    def recorder(label):
        async def close(self, *args, **kwargs):
            closed.append(label)

        return close

    monkeypatch.setattr(httpx.AsyncClient, "aclose", recorder("http"))
    monkeypatch.setattr(PublicPageReader, "aclose", recorder("pages"))
    monkeypatch.setattr(aioredis.Redis, "aclose", recorder("redis"))
    monkeypatch.setattr(AiohttpSession, "close", recorder("bot"))
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        assert closed == []
    assert sorted(closed) == ["bot", "http", "pages", "redis"]


async def test_lifespan_leaves_injected_resources(settings, monkeypatch):
    closed: list[str] = []

    async def close(self, *args, **kwargs):
        closed.append("x")

    for cls, name in (
        (httpx.AsyncClient, "aclose"),
        (PublicPageReader, "aclose"),
        (aioredis.Redis, "aclose"),
        (AiohttpSession, "close"),
    ):
        monkeypatch.setattr(cls, name, close)
    app = create_app(settings, gateway=object(), telegram_bot=object())
    async with app.router.lifespan_context(app):
        pass
    assert closed == []
