"""채널과 무관한 계정 단위 대화 세션 (REQ-CHN-001)."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dodeed.llm.types import Message, Role
from dodeed.sessions.models import ConversationMessage, ConversationRow, ConversationState


@dataclass
class Conversation:
    id: uuid.UUID
    user_id: uuid.UUID
    last_channel: str


async def get_or_create(
    db: AsyncSession, user_id: uuid.UUID, channel: str, now: datetime | None = None
) -> Conversation:
    """사용자당 대화 하나를 돌려주고 `last_channel`을 갱신한다.

    동시 호출에도 안전하도록 INSERT ... ON CONFLICT (user_id) DO UPDATE를 쓴다.
    """
    stamp = now if now is not None else func.now()
    stmt = (
        insert(ConversationRow)
        .values(id=uuid.uuid4(), user_id=user_id, last_channel=channel, updated_at=stamp)
        .on_conflict_do_update(
            index_elements=[ConversationRow.user_id],
            set_={"last_channel": channel, "updated_at": stamp},
        )
        .returning(ConversationRow.id, ConversationRow.user_id, ConversationRow.last_channel)
    )
    row = (await db.execute(stmt)).one()
    return Conversation(id=row.id, user_id=row.user_id, last_channel=row.last_channel)


async def append(db: AsyncSession, conversation_id: uuid.UUID, role: Role, content: str) -> None:
    """메시지를 저장한다. `content`는 호출자가 이미 가명 처리(redact)한 텍스트여야 한다."""
    db.add(ConversationMessage(conversation_id=conversation_id, role=role, content=content))
    await db.flush()


async def history(db: AsyncSession, conversation_id: uuid.UUID, limit: int = 30) -> list[Message]:
    """최근 `limit`개 메시지를 오래된 것부터 최신 순으로 돌려준다."""
    rows = (
        await db.execute(
            select(ConversationMessage.role, ConversationMessage.content)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            .limit(limit)
        )
    ).all()
    return [Message(role=r.role, content=r.content) for r in reversed(rows)]


async def get_state(db: AsyncSession, conversation_id: uuid.UUID) -> dict:
    data = await db.scalar(
        select(ConversationState.data).where(ConversationState.conversation_id == conversation_id)
    )
    return data if data is not None else {}


async def set_state(db: AsyncSession, conversation_id: uuid.UUID, data: dict) -> None:
    """상태 전체를 `data`로 교체한다 (upsert)."""
    stmt = (
        insert(ConversationState)
        .values(conversation_id=conversation_id, data=data)
        .on_conflict_do_update(
            index_elements=[ConversationState.conversation_id], set_={"data": data}
        )
    )
    await db.execute(stmt)
