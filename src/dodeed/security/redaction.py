"""LLM으로 보내기 전에 개인정보를 자리표시자로 바꾸고, 응답에서 되돌린다 (REQ-SEC-002)."""

import re
from dataclasses import dataclass, field

PH_NAME = "[사용자_이름]"
PH_PHONE = "[사용자_전화번호]"
PH_ADDRESS = "[사용자_주소]"
PH_EMAIL = "[사용자_이메일]"
PH_CARD = "[카드번호]"

_PHONE_RE = re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
CARD_CANDIDATE_RE = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")


@dataclass
class UserPII:
    name: str | None
    phone: str | None
    email: str | None
    address: str | None


@dataclass
class Redacted:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)  # 자리표시자 -> 원래 값


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def is_card_number(candidate: str) -> bool:
    digits = candidate.replace(" ", "").replace("-", "")
    return digits.isdigit() and 13 <= len(digits) <= 19 and luhn_valid(digits)


class _Placeholders:
    """같은 자리표시자가 서로 다른 값을 가리키면 `_2`, `_3`처럼 번호를 붙인다."""

    def __init__(self) -> None:
        self.mapping: dict[str, str] = {}
        self._by_value: dict[tuple[str, str], str] = {}

    def get(self, base: str, value: str) -> str:
        key = (base, value)
        if key in self._by_value:
            return self._by_value[key]
        n = 1
        while True:
            ph = base if n == 1 else f"{base[:-1]}_{n}]"
            if ph not in self.mapping:
                break
            n += 1
        self.mapping[ph] = value
        self._by_value[key] = ph
        return ph


def redact(text: str, pii: UserPII) -> Redacted:
    ph = _Placeholders()

    # 1) 알고 있는 값은 그대로 일치하면 바꾼다.
    for value, base in (
        (pii.name, PH_NAME),
        (pii.phone, PH_PHONE),
        (pii.email, PH_EMAIL),
        (pii.address, PH_ADDRESS),
    ):
        if value:
            text = text.replace(value, ph.get(base, value))

    # 2) 패턴 (카드 -> 전화 -> 이메일)
    def card(m: re.Match[str]) -> str:
        return ph.get(PH_CARD, m.group(0)) if is_card_number(m.group(0)) else m.group(0)

    text = CARD_CANDIDATE_RE.sub(card, text)
    text = _PHONE_RE.sub(lambda m: ph.get(PH_PHONE, m.group(0)), text)
    text = _EMAIL_RE.sub(lambda m: ph.get(PH_EMAIL, m.group(0)), text)
    return Redacted(text=text, mapping=ph.mapping)


def restore(text: str, mapping: dict[str, str]) -> str:
    # 긴 자리표시자부터 되돌려 접두 충돌을 막는다.
    for placeholder in sorted(mapping, key=len, reverse=True):
        text = text.replace(placeholder, mapping[placeholder])
    return text
