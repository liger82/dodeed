import pytest

from dodeed.security.egress import SecretRegistry


@pytest.fixture
def registry() -> SecretRegistry:
    return SecretRegistry(b"test-hmac-key")
