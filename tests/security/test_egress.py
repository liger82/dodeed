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


@pytest.mark.parametrize(
    "template",
    [
        "key=SECRET",
        "https://x.test/cb?token=SECRET&a=1",
        "ends with SECRET.",
        "`SECRET`",
        "https://api.test/v1/SECRET/items",
    ],
)
def test_registered_secret_blocked_when_glued_to_punctuation(registry, template):
    secret = "abc-secret-xyz-12345"
    registry.register(secret)
    with pytest.raises(EgressBlocked) as e:
        check_egress(template.replace("SECRET", secret), registry)
    assert e.value.reason == "registered_secret"
    assert secret not in str(e.value)


KOREAN_PROSE = (
    "강남역 근처에서 혼밥하기 좋은 국밥집을 찾았어요. 국물이 진하고 가격도 괜찮습니다. "
    "화장실은 건물 안에 있고, 점심시간에는 줄이 조금 있어요.\n"
)
SECRETS = ("abc-secret-xyz-12345", "naver-secret-abcdef123", "k" * 40)


def _counting(registry, monkeypatch) -> list[int]:
    calls = [0]
    original = registry._fp

    def spy(value: str) -> str:
        calls[0] += 1
        return original(value)

    monkeypatch.setattr(registry, "_fp", spy)
    return calls


def test_long_korean_text_scans_few_windows(registry, monkeypatch):
    for s in SECRETS:
        registry.register(s)
    text = (KOREAN_PROSE * (20_000 // len(KOREAN_PROSE) + 1))[:20_000]
    naive = sum(len(text) - len(s) + 1 for s in SECRETS)
    calls = _counting(registry, monkeypatch)
    check_egress(text, registry)
    # 공백 없이 이어진 긴 덩어리 안의 창만 지문을 계산한다 (순진한 방식 대비 90% 이상 감소)
    assert calls[0] < naive // 10, (calls[0], naive)


def test_secret_inside_long_korean_text_found(registry):
    secret = SECRETS[0]
    registry.register(secret)
    text = KOREAN_PROSE * 150 + f"설정값은(key={secret}). " + KOREAN_PROSE * 150
    with pytest.raises(EgressBlocked) as e:
        check_egress(text, registry)
    assert e.value.reason == "registered_secret"


@pytest.mark.parametrize("secret", ["has space-123456", "tab\tsecret-1234", "line\nbreak-12345"])
def test_secret_with_whitespace_rejected(registry, secret):
    with pytest.raises(ValueError):
        registry.register(secret)
