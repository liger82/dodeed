import pytest

from dodeed.config import Settings
from dodeed.security.egress import EgressBlocked, check_egress, registry_from_settings


def test_registered_secret_blocked(registry):
    registry.register("naver-secret-abcdef123")
    with pytest.raises(EgressBlocked) as e:
        check_egress('결과: {"key": "naver-secret-abcdef123"}', registry)
    assert e.value.reason == "registered_secret"
    assert "naver-secret-abcdef123" not in str(e.value)


def test_card_number_blocked(registry):
    with pytest.raises(EgressBlocked) as e:
        check_egress("카드 4111-1111-1111-1111", registry)
    assert e.value.reason == "card_number"
    assert "4111" not in str(e.value)


def test_invalid_card_like_number_passes(registry):
    check_egress("주문번호 1234 5678 9012 3456", registry)


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abcdefghijklmnop1234",
        "key=sk-abcdefghijklmnopqrstu",
        "AKIAIOSFODNN7EXAMPLE1",
    ],
)
def test_token_prefix_blocked(registry, text):
    with pytest.raises(EgressBlocked) as e:
        check_egress(text, registry)
    assert e.value.reason == "token_prefix"


def test_short_token_after_prefix_passes(registry):
    check_egress("sk-short", registry)


def test_normal_text_passes(registry):
    check_egress("강남역 근처 혼밥 식당 3곳", registry)


def test_short_registered_secret_not_matched(registry):
    registry.register("abc")  # 8자 미만 토큰은 비교하지 않는다
    check_egress("abc def", registry)


def test_registry_does_not_store_plaintext(registry):
    registry.register("plain-secret-value")
    assert "plain-secret-value" not in repr(vars(registry))


def test_registry_from_settings_registers_all_secret_fields(settings: Settings):
    reg = registry_from_settings(settings)
    for value in (
        settings.telegram_bot_token,
        settings.telegram_webhook_secret,
        settings.kakao_rest_api_key,
        settings.naver_client_id,
        settings.naver_client_secret,
        settings.localdata_api_key,
        settings.secret_hmac_key,
    ):
        assert reg.contains_in(f"x {value} y"), value
    assert "test-telegram-token" not in repr(vars(reg))
