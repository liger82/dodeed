from functools import lru_cache
from typing import Literal, Self

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENCRYPTED_PREFIX = "encrypted:"
ENCRYPTED_MESSAGE = "dotenvx로 암호화된 값입니다. `dotenvx run -- <명령>`으로 실행해 주세요."


class LLMKeys(BaseSettings):
    """LLM 키와 모델 설정. 서버(Settings), live 테스트, 평가 스크립트가 함께 쓴다.

    키는 `DODEED_*`와 접두사 없는 `OPENAI_API_KEY`/`GEMINI_API_KEY`를 모두 받고,
    둘 다 있으면 `DODEED_*`가 이긴다 (별칭 순서). 셸에 있는 `OPENAI_API_KEY`도 읽힌다.
    """

    # hide_input_in_errors: 검증 오류 메시지에 입력값(키)을 넣지 않는다
    model_config = SettingsConfigDict(
        env_prefix="DODEED_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    openai_api_key: str | None = Field(
        default=None,
        min_length=8,
        validation_alias=AliasChoices("DODEED_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    # Gemini API 결제 등급 키만 쓴다
    gemini_api_key: str | None = Field(
        default=None,
        min_length=8,
        validation_alias=AliasChoices("DODEED_GEMINI_API_KEY", "GEMINI_API_KEY"),
    )
    openai_model: str = "gpt-6-luna"
    gemini_model: str = "gemini-3.5-flash-lite"
    # None이면 요청에 reasoning_effort를 넣지 않는다. 도구가 있는 요청은 이 값과 관계없이 "none"
    openai_reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None

    @field_validator("openai_api_key", "gemini_api_key", mode="before")
    @classmethod
    def _clean_key(cls, value: object) -> object:
        # 앞뒤 공백은 지우고, 빈 값(.env.example 그대로)은 없는 것으로 본다 (fake로 시작할 수 있게).
        # dotenvx 암호값은 복호화 없이 실행했다는 뜻이다. 메시지에 값을 넣지 않는다.
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            return None
        if value.startswith(ENCRYPTED_PREFIX):
            raise ValueError(ENCRYPTED_MESSAGE)
        return value


class Settings(LLMKeys):
    """서버 설정. LLM 키와 모델 필드는 LLMKeys에서 물려받는다."""

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
    # LLM 설정은 모두 기본값이 있다. live일 때만 두 API 키가 필요하다.
    llm: Literal["fake", "live"] = "fake"
    llm_timeout_s: float = 30.0

    @model_validator(mode="after")
    def _live_needs_both_keys(self) -> Self:
        if self.llm == "live" and (self.openai_api_key is None or self.gemini_api_key is None):
            raise ValueError("llm=live에는 openai_api_key와 gemini_api_key가 모두 필요합니다")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
