"""LLM으로 나가는 텍스트에서 카드번호, 토큰, 등록된 비밀값을 막는다 (REQ-SEC-001)."""

import hashlib
import hmac
import re
from typing import TYPE_CHECKING

from dodeed.security.redaction import CARD_CANDIDATE_RE, is_card_number

if TYPE_CHECKING:
    from dodeed.config import Settings

_MIN_TOKEN_LEN = 8
_SPLIT_RE = re.compile(r"[\s\"',;:()\[\]{}<>]+")
_TOKEN_PREFIX_RE = re.compile(r"(?:Bearer\s+|sk-|AKIA)[A-Za-z0-9._\-]{16,}")


class EgressBlocked(Exception):  # noqa: N818 - 계약된 이름
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SecretRegistry:
    """비밀값의 원문은 보관하지 않고 HMAC-SHA256 지문만 보관한다."""

    def __init__(self, hmac_key: bytes) -> None:
        self._key = hmac_key
        self._fingerprints: set[str] = set()

    def _fp(self, value: str) -> str:
        return hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()

    def register(self, secret: str) -> None:
        self._fingerprints.add(self._fp(secret))

    def contains_in(self, text: str) -> bool:
        for token in _SPLIT_RE.split(text):
            if len(token) >= _MIN_TOKEN_LEN and self._fp(token) in self._fingerprints:
                return True
        return False


def check_egress(text: str, registry: SecretRegistry) -> None:
    """문제가 있으면 EgressBlocked를 던진다. reason에는 규칙 이름만 담고 값은 담지 않는다."""
    if any(is_card_number(m.group(0)) for m in CARD_CANDIDATE_RE.finditer(text)):
        raise EgressBlocked("card_number")
    if _TOKEN_PREFIX_RE.search(text):
        raise EgressBlocked("token_prefix")
    if registry.contains_in(text):
        raise EgressBlocked("registered_secret")


def registry_from_settings(settings: "Settings") -> SecretRegistry:
    registry = SecretRegistry(settings.secret_hmac_key.encode())
    for value in (
        settings.telegram_bot_token,
        settings.telegram_webhook_secret,
        settings.kakao_rest_api_key,
        settings.naver_client_id,
        settings.naver_client_secret,
        settings.localdata_api_key,
        settings.secret_hmac_key,
    ):
        registry.register(value)
    return registry
