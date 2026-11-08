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


OPENAI_KEY = "test-openai-key-123"
GEMINI_KEY = "test-gemini-key-456"


@pytest.mark.parametrize(
    "keys",
    [{"openai_api_key": OPENAI_KEY}, {"gemini_api_key": GEMINI_KEY}, {}],
)
def test_live_requires_both_keys(keys):
    with pytest.raises(ValidationError):
        _settings(llm="live", **keys)


def test_live_with_both_keys_ok():
    s = _settings(llm="live", openai_api_key=OPENAI_KEY, gemini_api_key=GEMINI_KEY)
    assert s.llm == "live"
    assert s.openai_api_key == OPENAI_KEY
    assert s.gemini_api_key == GEMINI_KEY


def test_fake_needs_no_llm_keys():
    s = _settings()
    assert s.llm == "fake"
    assert s.openai_api_key is None
    assert s.gemini_api_key is None


@pytest.mark.parametrize("field", ["openai_api_key", "gemini_api_key"])
def test_llm_key_min_length(field):
    with pytest.raises(ValidationError):
        _settings(**{field: "1234567"})


def test_model_names_default():
    s = _settings()
    assert s.openai_model == "gpt-6-luna"
    assert s.gemini_model == "gemini-3.5-flash-lite"
    assert s.llm_timeout_s == 60.0
    assert s.openai_reasoning_effort is None


def test_reasoning_effort_validated():
    assert _settings(openai_reasoning_effort="low").openai_reasoning_effort == "low"
    with pytest.raises(ValidationError):
        _settings(openai_reasoning_effort="extreme")


def test_empty_llm_keys_from_env_mean_unset(monkeypatch):
    # .env.example은 두 키를 빈 값으로 둔다. 그대로 복사해도 fake로 시작돼야 한다.
    for key, value in VALID.items():
        monkeypatch.setenv(f"DODEED_{key.upper()}", value)
    monkeypatch.setenv("DODEED_OPENAI_API_KEY", "")
    monkeypatch.setenv("DODEED_GEMINI_API_KEY", "")
    s = Settings(_env_file=None)
    assert s.openai_api_key is None and s.gemini_api_key is None
