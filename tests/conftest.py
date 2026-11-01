from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from dodeed.app import create_app
from dodeed.config import Settings

TEST_DATABASE_URL = "postgresql+asyncpg://dodeed:dodeed@localhost:5432/dodeed_test"



@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        redis_url="redis://localhost:6379/1",
        web_base_url="http://test",
        telegram_bot_token="test-telegram-token",
        telegram_webhook_secret="test-webhook-secret",
        kakao_rest_api_key="test-kakao-key",
        naver_client_id="test-naver-id",
        naver_client_secret="test-naver-secret",
        localdata_api_key="test-localdata-key",
        secret_hmac_key="test-hmac-key",
    )


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def db_session(settings):
    """테스트마다 트랜잭션을 열고 끝나면 롤백한다 (commit도 savepoint로 처리)."""
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(settings):
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
