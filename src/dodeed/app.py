from fastapi import FastAPI

from dodeed.accounts.api import router as accounts_router
from dodeed.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="dodeed")
    app.state.settings = settings or get_settings()

    app.include_router(accounts_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
