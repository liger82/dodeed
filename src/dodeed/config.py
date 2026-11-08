from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
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
    # fake는 고정 응답 LLM(StaticLLM), live는 1차 OpenAI + 백업 Gemini.
    # 아래 LLM 설정은 모두 기본값이 있다. live일 때만 두 API 키가 필요하다.
    llm: Literal["fake", "live"] = "fake"
    openai_api_key: str | None = Field(default=None, min_length=8)
    # Gemini API 결제 등급 키만 쓴다
    gemini_api_key: str | None = Field(default=None, min_length=8)
    openai_model: str = "gpt-6-luna"
    gemini_model: str = "gemini-3.5-flash-lite"
    llm_timeout_s: float = 60.0
    # None이면 요청에 reasoning_effort를 넣지 않는다
    openai_reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None

    @field_validator("openai_api_key", "gemini_api_key", mode="before")
    @classmethod
    def _empty_key_is_unset(cls, value: object) -> object:
        # .env.example처럼 빈 값으로 둔 키는 없는 것으로 본다 (fake로 시작할 수 있게)
        return None if value == "" else value

    @model_validator(mode="after")
    def _live_needs_both_keys(self) -> Self:
        if self.llm == "live" and (self.openai_api_key is None or self.gemini_api_key is None):
            raise ValueError("llm=live에는 openai_api_key와 gemini_api_key가 모두 필요합니다")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
