import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dodeed.audit.models import AuditLog, AuditRef

AUDIT_RETENTION = timedelta(days=365 * 3)


async def _actor_ref(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    stmt = (
        insert(AuditRef)
        .values(user_id=user_id, actor_ref=uuid.uuid4())
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    await db.execute(stmt)
    ref = await db.scalar(
        AuditRef.__table__.select()
        .with_only_columns(AuditRef.actor_ref)
        .where(AuditRef.user_id == user_id)
    )
    return ref


async def record(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    tool: str,
    decision: str,
    approved: bool | None,
    detail: dict,
) -> None:
    """감사 로그를 남긴다. user_id 대신 가명 식별자(actor_ref)만 로그에 저장한다."""
    actor_ref = await _actor_ref(db, user_id)
    db.add(
        AuditLog(
            actor_ref=actor_ref, tool=tool, decision=decision, approved=approved, detail=detail
        )
    )
    await db.flush()


async def forget_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    """매핑만 지워 로그를 가명 처리한다. 로그 행은 보관 기간까지 남는다."""
    await db.execute(delete(AuditRef).where(AuditRef.user_id == user_id))


async def purge_expired(db: AsyncSession, now: datetime) -> int:
    result = await db.execute(delete(AuditLog).where(AuditLog.created_at < now - AUDIT_RETENTION))
    return result.rowcount
