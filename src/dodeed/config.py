from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DODEED_", env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    web_base_url: str
    # 빈 비밀값은 인증 우회가 되므로 길이와 형식을 강제한다 (REQ-SEC).
    # 모든 비밀값은 송출 검사 등록 최소 길이(8자) 이상이어야 한다.
    telegram_bot_token: str = Field(min_length=8)
    # 텔레그램이 허용하는 secret_token 문자 집합 (1~256자, A-Z a-z 0-9 _ -)
    telegram_webhook_secret: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")
    kakao_rest_api_key: str = Field(min_length=8)
    naver_client_id: str = Field(min_length=8)
    naver_client_secret: str = Field(min_length=8)
    localdata_api_key: str = Field(min_length=8)
    # 세션 쿠키 서명과 비밀값 지문에 쓰는 키
    secret_hmac_key: str = Field(min_length=32)
    # 실제 모델 연결 전까지는 고정 응답 LLM만 쓴다. 기본값이 있는 유일한 설정이다.
    llm: Literal["fake"] = "fake"


@lru_cache
def get_settings() -> Settings:
    return Settings()
