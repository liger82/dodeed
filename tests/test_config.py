import pytest
from pydantic import ValidationError

from dodeed.config import Settings


def test_settings_requires_secrets(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("DODEED_"):
            monkeypatch.delenv(key)
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    missing = {e["loc"][0] for e in exc.value.errors()}
    assert {"secret_hmac_key", "telegram_webhook_secret", "telegram_bot_token"} <= missing


VALID = dict(
    database_url="postgresql+asyncpg://x/y",
    redis_url="redis://localhost/0",
    web_base_url="http://test",
    telegram_bot_token="123456789:test-telegram-token",
    telegram_webhook_secret="test-webhook-secret",
    kakao_rest_api_key="test-kakao-key",
    naver_client_id="test-naver-id",
    naver_client_secret="test-naver-secret",
    localdata_api_key="test-localdata-key",
    secret_hmac_key="test-hmac-key-0123456789abcdefghij",
)


def _settings(**over):
    return Settings(_env_file=None, **{**VALID, **over})


def test_valid_settings_accepted():
    assert _settings().telegram_webhook_secret == "test-webhook-secret"


@pytest.mark.parametrize("value", ["", "short", "x" * 31])
def test_short_hmac_key_rejected(value):
    with pytest.raises(ValidationError):
        _settings(secret_hmac_key=value)


@pytest.mark.parametrize("value", ["", "has space", "slash/x", "점", "a" * 257])
def test_bad_webhook_secret_rejected(value):
    with pytest.raises(ValidationError):
        _settings(telegram_webhook_secret=value)


@pytest.mark.parametrize(
    "field",
    [
        "telegram_bot_token",
        "kakao_rest_api_key",
        "naver_client_id",
        "naver_client_secret",
        "localdata_api_key",
    ],
)
@pytest.mark.parametrize("value", ["", "1234567"])
def test_short_api_secrets_rejected(field, value):
    with pytest.raises(ValidationError):
        _settings(**{field: value})
