"""LLM으로 보내기 전에 개인정보를 자리표시자로 바꾸고, 응답에서 되돌린다 (REQ-SEC-002)."""

import re
from dataclasses import dataclass, field

PH_NAME = "[사용자_이름]"
PH_PHONE = "[사용자_전화번호]"
PH_ADDRESS = "[사용자_주소]"
PH_EMAIL = "[사용자_이메일]"
PH_CARD = "[카드번호]"

# 공백, 점, 하이픈 구분자와 +82 국가번호(앞자리 0 생략) 형태를 허용한다.
_PHONE_RE = re.compile(r"(?<!\d)(?:\+82[ .-]?|0)1[016789][ .-]?\d{3,4}[ .-]?\d{4}(?!\d)")
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


def _phone_digits(value: str) -> str:
    """구분자를 없애고 +82 형태를 국내 형태(0으로 시작)로 맞춘다."""
    digits = re.sub(r"\D", "", value)
    if value.strip().startswith("+82") and digits.startswith("82"):
        digits = "0" + digits[2:]
    return digits


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


_NUMBERED_RE = re.compile(r"^(\[.+)_\d+\]$")


def _base_of(placeholder: str) -> str:
    m = _NUMBERED_RE.match(placeholder)
    return f"{m.group(1)}]" if m else placeholder


# 이보다 짧은 이름은 일반 낱말과 겹치므로("김" → "김치찌개") 가리지 않는다
_MIN_NAME_LEN = 2


class _Placeholders:
    """같은 자리표시자가 서로 다른 값을 가리키면 `_2`, `_3`처럼 번호를 붙인다."""

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        # 이미 있는 대응표를 받으면 그 위에 이어서 번호를 붙인다 (같은 값은 같은 자리표시자)
        self.mapping: dict[str, str] = mapping if mapping is not None else {}
        self._by_value: dict[tuple[str, str], str] = {
            (_base_of(ph), value): ph for ph, value in self.mapping.items()
        }

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


def redact(text: str, pii: UserPII, mapping: dict[str, str] | None = None) -> Redacted:
    """mapping을 주면 그 대응표를 이어서 쓴다 (같은 턴의 다른 텍스트를 가릴 때)."""
    ph = _Placeholders(mapping)
    name = pii.name if pii.name and len(pii.name.strip()) >= _MIN_NAME_LEN else None

    # 1) 알고 있는 값은 그대로 일치하면 바꾼다.
    for value, base in (
        (name, PH_NAME),
        (pii.email, PH_EMAIL),
        (pii.address, PH_ADDRESS),
    ):
        if value:
            text = text.replace(value, ph.get(base, value))

    known_phone = _phone_digits(pii.phone) if pii.phone else None
    if pii.phone:
        text = text.replace(pii.phone, ph.get(PH_PHONE, pii.phone))

    # 2) 패턴 (카드 -> 전화 -> 이메일)
    def card(m: re.Match[str]) -> str:
        return ph.get(PH_CARD, m.group(0)) if is_card_number(m.group(0)) else m.group(0)

    text = CARD_CANDIDATE_RE.sub(card, text)

    def phone(m: re.Match[str]) -> str:
        raw = m.group(0)
        if known_phone and pii.phone and _phone_digits(raw) == known_phone:
            return ph.get(PH_PHONE, pii.phone)  # 다른 표기의 본인 번호
        return ph.get(PH_PHONE, raw)

    text = _PHONE_RE.sub(phone, text)
    text = _EMAIL_RE.sub(lambda m: ph.get(PH_EMAIL, m.group(0)), text)
    return Redacted(text=text, mapping=ph.mapping)


def restore(text: str, mapping: dict[str, str]) -> str:
    # 긴 자리표시자부터 되돌려 접두 충돌을 막는다.
    for placeholder in sorted(mapping, key=len, reverse=True):
        text = text.replace(placeholder, mapping[placeholder])
    return text
