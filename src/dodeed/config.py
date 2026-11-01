from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DODEED_", env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    web_base_url: str
    telegram_bot_token: str
    telegram_webhook_secret: str
    kakao_rest_api_key: str
    naver_client_id: str
    naver_client_secret: str
    localdata_api_key: str
    secret_hmac_key: str
    # 실제 모델 연결 전까지는 고정 응답 LLM만 쓴다. 기본값이 있는 유일한 설정이다.
    llm: Literal["fake"] = "fake"


@lru_cache
def get_settings() -> Settings:
    return Settings()
