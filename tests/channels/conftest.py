import httpx
import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from dodeed.app import create_app
from dodeed.channels.gateway import Gateway
from dodeed.channels.messages import OutText
from dodeed.db import get_db


class FakeOrchestrator:
    def __init__(self):
        self.calls = []

    async def handle(self, db, user_id, conversation, text, pii):
        self.calls.append((user_id, conversation, text, pii))
        return [OutText(text=f"echo:{text}")]


class FakeBot:
    """aiogram Bot 대역: 호출만 기록한다 (네트워크 없음)."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append(("send_message", {"chat_id": chat_id, "text": text, **kwargs}))

    async def answer_callback_query(self, callback_query_id, **kwargs):
        self.calls.append(
            ("answer_callback_query", {"callback_query_id": callback_query_id, **kwargs})
        )


@pytest_asyncio.fixture
async def redis_client(settings):
    r = aioredis.from_url(settings.redis_url)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


@pytest.fixture
def orch():
    return FakeOrchestrator()


@pytest.fixture
def gateway(orch, settings, redis_client):
    return Gateway(orch, settings, redis_client)


@pytest.fixture
def fake_bot():
    return FakeBot()


@pytest_asyncio.fixture
async def channel_client(settings, db_session, gateway, fake_bot):
    app = create_app(settings, gateway=gateway, telegram_bot=fake_bot)
    app.dependency_overrides[get_db] = lambda: db_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def logged_in_client(channel_client, db_session, signed_up_user_id):
    r = await channel_client.post(
        "/api/login", json={"email": "svc@example.com", "password": "pw-12345678"}
    )
    assert r.status_code == 200
    return channel_client
