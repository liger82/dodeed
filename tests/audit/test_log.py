import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from dodeed.audit.log import AUDIT_RETENTION, forget_user, purge_expired, record


async def _record(db, user_id, **kw):
    await record(
        db,
        user_id=user_id,
        tool=kw.get("tool", "restaurant.search"),
        decision="allow",
        approved=None,
        detail=kw.get("detail", {"query": "강남 한식"}),
    )


async def _ref(db, user_id):
    r = await db.execute(
        text("SELECT actor_ref FROM audit_refs WHERE user_id = :u"), {"u": user_id}
    )
    return r.scalar_one_or_none()


async def test_record_uses_actor_ref_not_user_id(db_session, user_id):
    await _record(db_session, user_id)
    await _record(db_session, user_id)
    ref = await _ref(db_session, user_id)
    assert ref is not None and ref != user_id
    rows = (
        await db_session.execute(
            text("SELECT actor_ref, row_to_json(audit_log)::text FROM audit_log")
        )
    ).all()
    assert len(rows) == 2
    for actor_ref, as_text in rows:
        assert actor_ref == ref
        assert str(user_id) not in as_text


async def test_update_is_rejected(db_session, user_id):
    await _record(db_session, user_id)
    with pytest.raises(DBAPIError):
        await db_session.execute(text("UPDATE audit_log SET tool = 'x'"))


async def test_delete_recent_row_is_rejected(db_session, user_id):
    await _record(db_session, user_id)
    with pytest.raises(DBAPIError):
        await db_session.execute(text("DELETE FROM audit_log"))


async def test_forget_user_unlinks_rows(db_session, user_id):
    await _record(db_session, user_id)
    ref = await _ref(db_session, user_id)
    await forget_user(db_session, user_id)
    assert await _ref(db_session, user_id) is None
    found = await db_session.execute(
        text("SELECT user_id FROM audit_refs WHERE actor_ref = :r"), {"r": ref}
    )
    assert found.first() is None
    n = await db_session.execute(
        text("SELECT count(*) FROM audit_log WHERE actor_ref = :r"),
        {"r": ref},
    )
    assert n.scalar_one() == 1


async def test_purge_expired_removes_only_older_than_3_years(db_session, user_id, now):
    assert AUDIT_RETENTION == timedelta(days=365 * 3)
    actor = uuid.uuid4()
    for delta in (AUDIT_RETENTION + timedelta(days=1), AUDIT_RETENTION - timedelta(days=1)):
        await db_session.execute(
            text(
                "INSERT INTO audit_log (actor_ref, created_at, tool, decision, detail) "
                "VALUES (:a, :c, 't', 'allow', '{}')"
            ),
            {"a": actor, "c": now - delta},
        )
    assert await purge_expired(db_session, now) == 1
    n = await db_session.execute(
        text("SELECT count(*) FROM audit_log WHERE actor_ref = :a"),
        {"a": actor},
    )
    assert n.scalar_one() == 1
