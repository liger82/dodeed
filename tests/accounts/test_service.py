import re
from datetime import timedelta

from dodeed.accounts.service import (
    LINK_CODE_TTL,
    issue_link_code,
    redeem_link_code,
    resolve_user,
    signup,
    unlink,
)


async def test_link_code_is_6_digits_and_expires_in_10_minutes(db_session, user_id, now):
    assert LINK_CODE_TTL == timedelta(minutes=10)
    code = await issue_link_code(db_session, user_id, now)
    assert re.fullmatch(r"\d{6}", code)
    late = now + timedelta(minutes=10, seconds=1)
    assert await redeem_link_code(db_session, code, "telegram", "111", late) is None


async def test_link_code_valid_at_exactly_ttl(db_session, user_id, now):
    code = await issue_link_code(db_session, user_id, now)
    assert await redeem_link_code(db_session, code, "telegram", "111", now + LINK_CODE_TTL) == (
        user_id
    )


async def test_link_code_single_use(db_session, user_id, now):
    code = await issue_link_code(db_session, user_id, now)
    assert await redeem_link_code(db_session, code, "telegram", "111", now) == user_id
    assert await redeem_link_code(db_session, code, "telegram", "222", now) is None


async def test_unlink_stops_resolution(db_session, user_id, now):
    code = await issue_link_code(db_session, user_id, now)
    await redeem_link_code(db_session, code, "telegram", "111", now)
    assert await resolve_user(db_session, "telegram", "111") == user_id
    await unlink(db_session, user_id, "telegram")
    assert await resolve_user(db_session, "telegram", "111") is None


async def test_channel_linked_to_other_user_is_rejected(db_session, user_id, now):
    other = await signup(db_session, "other@example.com", "pw-12345678", "다른 사람")
    await redeem_link_code(
        db_session, await issue_link_code(db_session, user_id, now), "telegram", "111", now
    )
    code = await issue_link_code(db_session, other, now)
    assert await redeem_link_code(db_session, code, "telegram", "111", now) is None
    assert await resolve_user(db_session, "telegram", "111") == user_id


async def test_relinking_same_user_returns_user_id(db_session, user_id, now):
    await redeem_link_code(
        db_session, await issue_link_code(db_session, user_id, now), "telegram", "111", now
    )
    code = await issue_link_code(db_session, user_id, now)
    assert await redeem_link_code(db_session, code, "telegram", "111", now) == user_id


async def test_issue_regenerates_on_collision(db_session, user_id, now, monkeypatch):
    first = await issue_link_code(db_session, user_id, now)
    seq = iter([int(first), int(first), 123456])
    monkeypatch.setattr("dodeed.accounts.service.secrets.randbelow", lambda n: next(seq))
    assert await issue_link_code(db_session, user_id, now) == "123456"


async def test_code_is_zero_padded(db_session, user_id, now, monkeypatch):
    monkeypatch.setattr("dodeed.accounts.service.secrets.randbelow", lambda n: 42)
    assert await issue_link_code(db_session, user_id, now) == "000042"


class _VerifySpy:
    def __init__(self, inner):
        self._inner = inner
        self.verified: list[str] = []

    def hash(self, password):
        return self._inner.hash(password)

    def verify(self, hashed, password):
        self.verified.append(hashed)
        return self._inner.verify(hashed, password)


async def test_authenticate_unknown_email_still_verifies_once(db_session, monkeypatch):
    from dodeed.accounts import service

    spy = _VerifySpy(service._hasher)
    monkeypatch.setattr(service, "_hasher", spy)
    assert await service.authenticate(db_session, "nobody@example.com", "pw-12345678") is None
    # 이메일이 없어도 같은 비용의 검증을 한 번 해서 응답 시간으로 가입 여부가 드러나지 않게 한다
    assert len(spy.verified) == 1
    assert spy.verified[0].startswith("$argon2")


async def test_authenticate_known_email_verifies_once(db_session, signed_up_user_id, monkeypatch):
    from dodeed.accounts import service

    spy = _VerifySpy(service._hasher)
    monkeypatch.setattr(service, "_hasher", spy)
    assert await service.authenticate(db_session, "svc@example.com", "pw-12345678") == (
        signed_up_user_id
    )
    assert await service.authenticate(db_session, "svc@example.com", "wrong-password") is None
    assert len(spy.verified) == 2
