from dodeed.security.redaction import UserPII, redact, restore

EMPTY = UserPII(None, None, None, None)


def test_known_pii_replaced():
    r = redact(
        "김도디 010-1234-5678로 예약",
        UserPII(name="김도디", phone="010-1234-5678", email=None, address=None),
    )
    assert r.text == "[사용자_이름] [사용자_전화번호]로 예약"
    assert restore(r.text, r.mapping) == "김도디 010-1234-5678로 예약"


def test_unknown_phone_pattern_replaced():
    assert "[사용자_전화번호]" in redact("연락처 01098765432", EMPTY).text


def test_email_and_address_replaced():
    pii = UserPII(None, None, None, address="서울시 강남구 역삼동 1")
    r = redact("a@b.com 서울시 강남구 역삼동 1", pii)
    assert r.text == "[사용자_이메일] [사용자_주소]"
    assert restore(r.text, r.mapping) == "a@b.com 서울시 강남구 역삼동 1"


def test_luhn_valid_card_replaced_invalid_kept():
    assert "[카드번호]" in redact("4111 1111 1111 1111", EMPTY).text
    assert "1234 5678 9012 3456" in redact("1234 5678 9012 3456", EMPTY).text


def test_card_with_hyphens_replaced():
    assert redact("4111-1111-1111-1111", EMPTY).text == "[카드번호]"


def test_two_different_phones_numbered_and_lossless():
    text = "010-1111-2222 그리고 010-3333-4444 그리고 010-1111-2222"
    r = redact(text, EMPTY)
    assert r.text == "[사용자_전화번호] 그리고 [사용자_전화번호_2] 그리고 [사용자_전화번호]"
    assert restore(r.text, r.mapping) == text


def test_known_phone_and_unknown_phone_distinct():
    pii = UserPII(None, "010-1111-2222", None, None)
    text = "010-1111-2222 / 010-3333-4444"
    r = redact(text, pii)
    assert r.text == "[사용자_전화번호] / [사용자_전화번호_2]"
    assert restore(r.text, r.mapping) == text


import pytest  # noqa: E402


@pytest.mark.parametrize(
    "phone",
    ["010 1234 5678", "010.1234.5678", "+82 10-1234-5678", "+82-10-1234-5678", "+821012345678"],
)
def test_phone_formats_redacted(phone):
    r = redact(f"전화 {phone} 입니다", EMPTY)
    assert r.text == "전화 [사용자_전화번호] 입니다"
    assert restore(r.text, r.mapping) == f"전화 {phone} 입니다"


@pytest.mark.parametrize(
    "variant", ["010 1234 5678", "010.1234.5678", "+82 10-1234-5678", "01012345678"]
)
def test_known_phone_matches_other_formats(variant):
    pii = UserPII(None, "010-1234-5678", None, None)
    r = redact(f"번호 {variant}", pii)
    assert r.text == "번호 [사용자_전화번호]"
    assert r.mapping == {"[사용자_전화번호]": "010-1234-5678"}


def test_known_phone_in_other_format_and_unknown_phone_distinct():
    pii = UserPII(None, "+82 10-1234-5678", None, None)
    r = redact("010-1234-5678 / 010-9999-8888", pii)
    assert r.text == "[사용자_전화번호] / [사용자_전화번호_2]"


def test_one_char_name_not_redacted():
    pii = UserPII(name="김", phone=None, email=None, address=None)
    r = redact("강남역 김치찌개 맛집", pii)
    assert r.text == "강남역 김치찌개 맛집"
    assert r.mapping == {}


def test_two_char_name_redacted():
    pii = UserPII(name="김철", phone=None, email=None, address=None)
    assert redact("나는 김철", pii).text == "나는 [사용자_이름]"


def test_name_short_after_strip_not_redacted():
    pii = UserPII(name=" 김 ", phone=None, email=None, address=None)
    assert redact("나 김 이야", pii).text == "나 김 이야"


def test_redact_extends_existing_mapping_consistently():
    pii = UserPII(name="홍대감", phone=None, email=None, address=None)
    first = redact("홍대감 010-1111-2222", pii)
    second = redact("다시 010-3333-4444 그리고 홍대감, 010-1111-2222", pii, first.mapping)
    assert second.mapping is first.mapping
    assert second.text == "다시 [사용자_전화번호_2] 그리고 [사용자_이름], [사용자_전화번호]"
    assert restore(second.text, second.mapping) == "다시 010-3333-4444 그리고 홍대감, 010-1111-2222"
