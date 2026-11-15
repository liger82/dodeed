import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from dodeed.db import Base


class AuditRef(Base):
    """사용자와 감사 로그 식별자의 매핑. 사용자 삭제 후에도 로그가 남도록 FK를 걸지 않는다."""

    __tablename__ = "audit_refs"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    actor_ref: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_ref: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False)
