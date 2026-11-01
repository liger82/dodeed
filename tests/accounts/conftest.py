import pytest_asyncio

from dodeed.accounts.service import signup


@pytest_asyncio.fixture
async def user_id(db_session):
    """users FK를 만족하도록 실제로 가입시킨 사용자."""
    return await signup(db_session, "svc@example.com", "pw-12345678", "서비스")
