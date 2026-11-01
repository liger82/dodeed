import os
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

TEST_DATABASE_URL = "postgresql+asyncpg://dodeed:dodeed@localhost:5432/dodeed_test"
os.environ["DODEED_DATABASE_URL"] = TEST_DATABASE_URL

from dodeed.app import create_app  # noqa: E402
from dodeed.config import Settings  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url=TEST_DATABASE_URL)


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
