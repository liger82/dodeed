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
