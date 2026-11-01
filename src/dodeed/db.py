from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from dodeed.config import Settings


class Base(DeclarativeBase):
    pass


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url)


def async_session_factory(settings: Settings) -> async_sessionmaker:
    return async_sessionmaker(create_engine(settings), expire_on_commit=False)
