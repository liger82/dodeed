import asyncio
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from dodeed.llm.types import Message
from dodeed.sessions import service
from dodeed.sessions.models import ConversationRow
from dodeed.sessions.service import (
    append,
    get_or_create,
    get_state,
    history,
    set_state,
)


async def test_same_conversation_across_channels(db_session, signed_up_user_id):
    a = await get_or_create(db_session, signed_up_user_id, "telegram")
    assert a.last_channel == "telegram"
    b = await get_or_create(db_session, signed_up_user_id, "web")
    assert a.id == b.id and b.last_channel == "web"
    c = await get_or_create(db_session, signed_up_user_id, "web")
    assert c.id == a.id


async def test_one_row_per_user(db_session, signed_up_user_id):
    await get_or_create(db_session, signed_up_user_id, "telegram")
    await get_or_create(db_session, signed_up_user_id, "web")
    rows = (await db_session.execute(select(ConversationRow))).scalars().all()
    assert len([r for r in rows if r.user_id == signed_up_user_id]) == 1


async def test_get_or_create_updates_updated_at(db_session, signed_up_user_id, now):
    await get_or_create(db_session, signed_up_user_id, "telegram", now=now)
    await get_or_create(db_session, signed_up_user_id, "web", now=now.replace(hour=13))
    row = (
        await db_session.execute(
            select(ConversationRow).where(ConversationRow.user_id == signed_up_user_id)
        )
    ).scalar_one()
    await db_session.refresh(row)
    assert row.updated_at == now.replace(hour=13)


async def test_state_round_trip(db_session, signed_up_user_id):
    conv = await get_or_create(db_session, signed_up_user_id, "web")
    assert await get_state(db_session, conv.id) == {}
    await set_state(db_session, conv.id, {"candidates": [{"name": "A"}], "location": {"lat": 1.5}})
    assert await get_state(db_session, conv.id) == {
        "candidates": [{"name": "A"}],
        "location": {"lat": 1.5},
    }
    await set_state(db_session, conv.id, {"location": {"lat": 2.0}})
    assert await get_state(db_session, conv.id) == {"location": {"lat": 2.0}}


async def test_history_limit_and_order(db_session, signed_up_user_id):
    conv = await get_or_create(db_session, signed_up_user_id, "web")
    assert await history(db_session, conv.id) == []
    for i in range(5):
        await append(db_session, conv.id, "user" if i % 2 == 0 else "assistant", f"m{i}")
    msgs = await history(db_session, conv.id, limit=3)
    assert msgs == [
        Message("user", "m2"),
        Message("assistant", "m3"),
        Message("user", "m4"),
    ]
    assert len(await history(db_session, conv.id)) == 5


async def test_user_delete_cascades(db_session, signed_up_user_id):
    conv = await get_or_create(db_session, signed_up_user_id, "web")
    await append(db_session, conv.id, "user", "hi")
    await set_state(db_session, conv.id, {"a": 1})
    await db_session.execute(text("DELETE FROM users WHERE id = :u"), {"u": signed_up_user_id})
    for table, col in [
        ("conversations", "id"),
        ("conversation_messages", "conversation_id"),
        ("conversation_state", "conversation_id"),
    ]:
        n = (
            await db_session.execute(
                text(f"SELECT count(*) FROM {table} WHERE {col} = :c"), {"c": conv.id}
            )
        ).scalar_one()
        assert n == 0, table


async def test_get_or_create_concurrent_is_race_safe(settings):
    """별도 연결 두 개가 동시에 처음 호출해도 대화는 하나다."""
    from dodeed.accounts.service import signup

    engine = create_async_engine(settings.database_url)
    email = f"race-{uuid.uuid4().hex[:8]}@example.com"
    async with AsyncSession(engine, expire_on_commit=False) as s:
        uid = await signup(s, email, "pw-12345678", "경합")
        await s.commit()
    try:

        async def call(ch: str):
            async with AsyncSession(engine, expire_on_commit=False) as s:
                c = await service.get_or_create(s, uid, ch)
                await s.commit()
                return c

        a, b = await asyncio.gather(call("telegram"), call("web"))
        assert a.id == b.id
    finally:
        async with AsyncSession(engine) as s:
            await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
            await s.commit()
        await engine.dispose()
