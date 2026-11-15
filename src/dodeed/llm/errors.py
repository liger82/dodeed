"""LLM 공급자 오류. 메시지에는 공급자 이름과 오류 종류/상태 코드만 넣는다 (키·본문 금지)."""

import logging
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

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


def transport_error(provider: str, exc: Exception) -> LLMProviderError:
    """httpx 전송 예외를 전환할 오류로 바꾼다. 로그에는 예외 종류 이름만 남긴다.

    호출하는 쪽은 `raise ... from None`으로 원래 예외(메시지에 URL 등이 있을 수 있음)를 끊는다.
    """
    kind: ProviderErrorKind = "timeout" if isinstance(exc, httpx.TimeoutException) else "connection"
    logger.warning("%s %s: %s", provider, kind, type(exc).__name__)
    return LLMProviderError(provider, kind)


def status_error(provider: str, status: int) -> LLMProviderError | LLMRequestError | None:
    """HTTP 상태 코드를 오류로 바꾼다. 성공(4xx/5xx가 아님)이면 None.

    429와 5xx는 전환할 오류, 그 밖의 4xx는 전환하지 않는 요청 오류다.
    """
    if status == 429:
        kind: ProviderErrorKind = "rate_limited"
    elif status >= 500:
        kind = "server_error"
    elif status >= 400:
        logger.warning("%s request rejected: status=%d", provider, status)
        return LLMRequestError(provider, status)
    else:
        return None
    logger.warning("%s %s: status=%d", provider, kind, status)
    return LLMProviderError(provider, kind, status)
