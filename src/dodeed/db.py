from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from dodeed.config import Settings


class Base(DeclarativeBase):
    pass


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url)


def async_session_factory(settings: Settings) -> async_sessionmaker:
    return async_sessionmaker(create_engine(settings), expire_on_commit=False)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """요청마다 세션을 열어 주는 FastAPI 의존성 (테스트에서는 override한다)."""
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        factory = request.app.state.session_factory = async_session_factory(
            request.app.state.settings
        )
    async with factory() as session:
        yield session
