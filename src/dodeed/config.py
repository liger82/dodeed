from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DODEED_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://dodeed:dodeed@localhost:5432/dodeed"
    redis_url: str = "redis://localhost:6379/0"
    web_base_url: str = "http://localhost:8000"
    telegram_bot_token: str = "test-telegram-token"
    telegram_webhook_secret: str = "test-webhook-secret"
    kakao_rest_api_key: str = "test-kakao-key"
    naver_client_id: str = "test-naver-id"
    naver_client_secret: str = "test-naver-secret"
    localdata_api_key: str = "test-localdata-key"
    secret_hmac_key: str = "test-hmac-key"


@lru_cache
def get_settings() -> Settings:
    return Settings()
