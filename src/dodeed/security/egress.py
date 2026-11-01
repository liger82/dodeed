"""LLM으로 나가는 텍스트에서 카드번호, 토큰, 등록된 비밀값을 막는다 (REQ-SEC-001)."""

import hashlib
import hmac
import re
from typing import TYPE_CHECKING

from dodeed.security.redaction import CARD_CANDIDATE_RE, is_card_number

if TYPE_CHECKING:
    from dodeed.config import Settings

_MIN_SECRET_LEN = 8
_TOKEN_PREFIX_RE = re.compile(r"(?:Bearer\s+|sk-|AKIA)[A-Za-z0-9._\-]{16,}")
_WHITESPACE_RE = re.compile(r"\s")
_RUN_RE = re.compile(r"\S+")  # 공백 없이 이어진 최대 덩어리


class EgressBlocked(Exception):  # noqa: N818 - 계약된 이름
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SecretRegistry:
    """비밀값의 원문은 보관하지 않고 HMAC-SHA256 지문만 보관한다."""

    def __init__(self, hmac_key: bytes) -> None:
        self._key = hmac_key
        self._fingerprints: set[str] = set()
        self._lengths: set[int] = set()  # 길이만 보관한다 (원문은 보관하지 않음)

    def _fp(self, value: str) -> str:
        return hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()

    def register(self, secret: str) -> None:
        # 공백이 든 비밀값은 받지 않는다: 검사가 공백으로 나뉜 덩어리 안에서만 이루어진다
        if _WHITESPACE_RE.search(secret):
            raise ValueError("공백이 들어 있는 비밀값은 등록할 수 없습니다")
        if len(secret) < _MIN_SECRET_LEN:
            return
        self._fingerprints.add(self._fp(secret))
        self._lengths.add(len(secret))

    def contains_in(self, text: str) -> bool:
        """공백 없이 이어진 덩어리마다, 등록된 각 길이의 모든 부분 문자열 지문을 비교한다.

        비밀값에는 공백이 없으므로 덩어리 밖에 걸친 창은 볼 필요가 없다. 가장 짧은 등록 길이보다
        짧은 덩어리(한국어 문장 대부분)는 건너뛴다. 구두점에 붙어 있어도 같은 덩어리 안이라 잡힌다.
        """
        if not self._lengths:
            return False
        lengths = sorted(self._lengths)
        shortest = lengths[0]
        for m in _RUN_RE.finditer(text):
            run = m.group(0)
            if len(run) < shortest:
                continue
            for length in lengths:
                for i in range(len(run) - length + 1):
                    if self._fp(run[i : i + length]) in self._fingerprints:
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
