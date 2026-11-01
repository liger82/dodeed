from aiogram import Bot
from fastapi import FastAPI

from dodeed.accounts.api import router as accounts_router
from dodeed.channels.gateway import Gateway
from dodeed.channels.telegram import router as telegram_router
from dodeed.channels.web import router as web_router
from dodeed.config import Settings, get_settings


def create_app(
    settings: Settings | None = None,
    *,
    gateway: Gateway | None = None,
    telegram_bot: Bot | None = None,
) -> FastAPI:
    app = FastAPI(title="dodeed")
    app.state.settings = settings or get_settings()

    app.state.gateway = gateway
    app.state.telegram_bot = telegram_bot

    app.include_router(accounts_router)
    app.include_router(telegram_router)
    app.include_router(web_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
