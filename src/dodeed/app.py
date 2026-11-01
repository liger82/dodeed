import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from aiogram import Bot
from fastapi import FastAPI

from dodeed.accounts.api import router as accounts_router
from dodeed.agent.orchestrator import Orchestrator
from dodeed.channels.gateway import Gateway
from dodeed.channels.telegram import router as telegram_router
from dodeed.channels.web import router as web_router
from dodeed.config import Settings, get_settings
from dodeed.llm.base import LLMClient
from dodeed.llm.guarded import GuardedLLM
from dodeed.llm.static import StaticLLM
from dodeed.policy.engine import PolicyEngine
from dodeed.policy.tools import ToolRegistry
from dodeed.reader.isolated import IsolatedReader
from dodeed.restaurant.candidates import RestaurantDeps
from dodeed.restaurant.hours import PageText
from dodeed.restaurant.tools import register as register_restaurant
from dodeed.security.egress import registry_from_settings
from dodeed.sources.kakao_local import KakaoLocal
from dodeed.sources.localdata import LocalData
from dodeed.sources.naver_search import NaverSearch
from dodeed.sources.public_page import PublicPageReader


def _quiet_http_logs() -> None:
    # httpx는 INFO 로그에 요청 URL(쿼리의 API 키 포함)을 남긴다
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _make_llm(settings: Settings) -> LLMClient:
    if settings.llm == "fake":
        return StaticLLM()
    raise ValueError(f"알 수 없는 LLM 설정: {settings.llm}")  # pragma: no cover


def build_orchestrator(
    settings: Settings,
    *,
    http: httpx.AsyncClient,
    page_reader: PageText,
    orchestrator_llm: LLMClient,
    reader_llm: LLMClient,
) -> Orchestrator:
    """오케스트레이터와 음식점 도구를 조립한다. 모든 LLM은 GuardedLLM으로 감싼다."""
    secrets = registry_from_settings(settings)
    reader = IsolatedReader(GuardedLLM(reader_llm, secrets, "reader"))
    deps = RestaurantDeps(
        kakao=KakaoLocal(http, settings),
        naver=NaverSearch(http, settings),
        localdata=LocalData(http, settings),
        reader=reader,
        page_reader=page_reader,
    )
    tools = ToolRegistry()
    register_restaurant(tools, deps)
    return Orchestrator(
        GuardedLLM(orchestrator_llm, secrets, "orchestrator"),
        tools,
        PolicyEngine(),
        kakao=deps.kakao,
    )


def create_app(
    settings: Settings | None = None,
    *,
    gateway: Gateway | None = None,
    telegram_bot: Bot | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    _quiet_http_logs()

    owned: list = []  # 앱이 만든 자원만 종료할 때 닫는다
    if gateway is None:
        # 시작할 때 네트워크를 쓰지 않는다: 클라이언트는 모두 처음 쓸 때 연결한다
        http = httpx.AsyncClient()
        page_reader = PublicPageReader()
        redis = aioredis.from_url(settings.redis_url)
        llm = _make_llm(settings)
        orchestrator = build_orchestrator(
            settings, http=http, page_reader=page_reader, orchestrator_llm=llm, reader_llm=llm
        )
        gateway = Gateway(orchestrator, settings, redis)
        owned += [http.aclose, page_reader.aclose, redis.aclose]
    if telegram_bot is None:
        telegram_bot = Bot(token=settings.telegram_bot_token)
        owned.append(telegram_bot.session.close)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        for close in reversed(owned):
            try:
                await close()
            except Exception:  # 종료 중 실패는 다른 자원 정리를 막지 않는다
                logging.getLogger(__name__).warning("자원 종료 실패", exc_info=True)

    app = FastAPI(title="dodeed", lifespan=lifespan)
    app.state.settings = settings
    app.state.gateway = gateway
    app.state.telegram_bot = telegram_bot

    app.include_router(accounts_router)
    app.include_router(telegram_router)
    app.include_router(web_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
