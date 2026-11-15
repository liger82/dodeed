import pytest
from pydantic import ValidationError

from dodeed.config import LLMKeys, Settings
from dodeed.security.egress import registry_from_settings


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
    assert s.llm_timeout_s == 30.0
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


# --- LLM 키: 접두사 없는 별칭, 공백, dotenvx 암호값 ---

ENCRYPTED_MSG = "dotenvx로 암호화된 값입니다. `dotenvx run -- <명령>`으로 실행해 주세요."


def _env_settings(monkeypatch, **env):
    for key, value in VALID.items():
        monkeypatch.setenv(f"DODEED_{key.upper()}", value)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


@pytest.mark.parametrize("cls", ["settings", "llm_keys"])
def test_unprefixed_key_aliases_used(monkeypatch, cls):
    env = {"OPENAI_API_KEY": OPENAI_KEY, "GEMINI_API_KEY": GEMINI_KEY}
    if cls == "settings":
        s = _env_settings(monkeypatch, **env)
    else:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        s = LLMKeys(_env_file=None)
    assert s.openai_api_key == OPENAI_KEY
    assert s.gemini_api_key == GEMINI_KEY


@pytest.mark.parametrize("cls", ["settings", "llm_keys"])
def test_prefixed_key_wins_over_unprefixed(monkeypatch, cls):
    env = {
        "OPENAI_API_KEY": "ambient-openai-key",
        "DODEED_OPENAI_API_KEY": OPENAI_KEY,
        "GEMINI_API_KEY": "ambient-gemini-key",
        "DODEED_GEMINI_API_KEY": GEMINI_KEY,
    }
    if cls == "settings":
        s = _env_settings(monkeypatch, **env)
    else:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        s = LLMKeys(_env_file=None)
    assert s.openai_api_key == OPENAI_KEY
    assert s.gemini_api_key == GEMINI_KEY


@pytest.mark.parametrize("env_name", ["DODEED_OPENAI_API_KEY", "GEMINI_API_KEY"])
@pytest.mark.parametrize("cls", ["settings", "llm_keys"])
def test_encrypted_key_rejected_without_value(monkeypatch, env_name, cls):
    value = "encrypted:BDQEsecretciphertext0123456789"
    monkeypatch.setenv(env_name, value)
    with pytest.raises(ValidationError) as exc:
        if cls == "settings":
            _env_settings(monkeypatch)
        else:
            LLMKeys(_env_file=None)
    text = str(exc.value)
    assert ENCRYPTED_MSG in text
    assert value not in text
    assert "BDQEsecret" not in text


def test_encrypted_key_rejected_via_constructor():
    with pytest.raises(ValidationError) as exc:
        _settings(openai_api_key="encrypted:abcdefgh12345")
    assert ENCRYPTED_MSG in str(exc.value)
    assert "abcdefgh12345" not in str(exc.value)


def test_whitespace_padded_key_stripped_and_registered(monkeypatch):
    s = _env_settings(
        monkeypatch,
        DODEED_LLM="live",
        OPENAI_API_KEY=f"  {OPENAI_KEY}\n",
        DODEED_GEMINI_API_KEY=f"\t{GEMINI_KEY} ",
    )
    assert s.openai_api_key == OPENAI_KEY
    assert s.gemini_api_key == GEMINI_KEY
    registry = registry_from_settings(s)
    assert registry.contains_in(f"leak {OPENAI_KEY}")
    assert registry.contains_in(f"leak {GEMINI_KEY}")


def test_whitespace_only_key_is_unset(monkeypatch):
    s = _env_settings(monkeypatch, OPENAI_API_KEY="   ")
    assert s.openai_api_key is None


def test_llm_keys_defaults():
    keys = LLMKeys(_env_file=None)
    assert keys.openai_api_key is None and keys.gemini_api_key is None
    assert keys.openai_model == "gpt-6-luna"
    assert keys.gemini_model == "gemini-3.5-flash-lite"
    assert keys.openai_reasoning_effort is None
