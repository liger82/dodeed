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
