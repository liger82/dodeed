import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from dodeed.app import create_app
from dodeed.config import Settings
from dodeed.db import get_db
from dodeed.security.egress import SecretRegistry

TEST_DATABASE_URL = "postgresql+asyncpg://dodeed:dodeed@localhost:5432/dodeed_test"
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


@pytest.fixture(scope="session", autouse=True)
def _migrate_test_db() -> None:
    """테스트 DB를 최신 마이그레이션으로 맞춘다 (이벤트 루프 밖의 동기 fixture)."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(cfg, "head")


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        redis_url="redis://localhost:6379/1",
        web_base_url="http://test",
        telegram_bot_token="123456789:test-telegram-token",
        telegram_webhook_secret="test-webhook-secret",
        kakao_rest_api_key="test-kakao-key",
        naver_client_id="test-naver-id",
        naver_client_secret="test-naver-secret",
        localdata_api_key="test-localdata-key",
        secret_hmac_key="test-hmac-key-0123456789abcdefghij",
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
async def client(settings, db_session):
    app = create_app(settings)
    app.dependency_overrides[get_db] = lambda: db_session
    transport = httpx.ASGITransport(app=app)
    # lifespan을 돌려 앱이 만든 자원(Redis 연결 등)을 테스트 끝에 닫는다
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture
def registry() -> SecretRegistry:
    return SecretRegistry(b"test-hmac-key")


@pytest_asyncio.fixture
async def signed_up_user_id(db_session) -> uuid.UUID:
    """users FK를 만족하도록 실제로 가입시킨 사용자."""
    from dodeed.accounts.service import signup

    return await signup(db_session, "svc@example.com", "pw-12345678", "서비스")
