"""LLM 공급자 오류. 메시지에는 공급자 이름과 오류 종류/상태 코드만 넣는다 (키·본문 금지)."""

from typing import Literal

ProviderErrorKind = Literal["timeout", "connection", "rate_limited", "server_error", "bad_output"]


class LLMProviderError(Exception):
    """백업 모델로 전환할 오류: 시간 초과, 연결 오류, 429, 5xx, 응답 형식 오류."""

    def __init__(self, provider: str, kind: ProviderErrorKind, status: int | None = None) -> None:
        self.provider = provider
        self.kind = kind
        self.status = status
        super().__init__(f"{provider} {kind or status}")


class LLMRequestError(Exception):
    """전환하지 않는 오류: 그 밖의 4xx (요청 자체가 잘못됨)."""

    def __init__(self, provider: str, status: int) -> None:
        self.provider = provider
        self.status = status
        super().__init__(f"{provider} {status}")
