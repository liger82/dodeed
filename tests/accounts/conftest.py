import pytest_asyncio


@pytest_asyncio.fixture
async def user_id(signed_up_user_id):
    """users FK를 만족하도록 실제로 가입시킨 사용자."""
    return signed_up_user_id
