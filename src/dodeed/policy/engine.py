import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import ForeignKey, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import UUID, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from dodeed.audit.log import record
from dodeed.db import Base
from dodeed.policy.tools import TASK_TOOLSETS, Grade, PermissionLevel, ToolRegistry, ToolSpec


class ServicePermission(Base):
    __tablename__ = "service_permissions"
    __table_args__ = (UniqueConstraint("user_id", "service"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    service: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False)


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"


@dataclass
class PolicyResult:
    decision: Decision
    reason: str


async def get_level(db: AsyncSession, user_id: uuid.UUID, service: str) -> PermissionLevel:
    level = await db.scalar(
        select(ServicePermission.level).where(
            ServicePermission.user_id == user_id, ServicePermission.service == service
        )
    )
    return PermissionLevel(level) if level else PermissionLevel.APPROVE


async def set_level(
    db: AsyncSession,
    user_id: uuid.UUID,
    service: str,
    level: PermissionLevel,
    registry: ToolRegistry,
) -> None:
    if level == PermissionLevel.INSTANT and not registry.has_instant_allowed(service):
        raise ValueError(f"즉시 실행을 허용하는 도구가 없는 서비스입니다: {service}")
    stmt = insert(ServicePermission).values(user_id=user_id, service=service, level=level.value)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "service"], set_={"level": level.value}
    )
    await db.execute(stmt)


class PolicyEngine:
    """LLM 밖에서 도구 호출을 허용/거부/승인 요청으로 판단하고 감사 로그를 남긴다."""

    async def check(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        task_type: str,
        spec: ToolSpec,
        approval_token: str | None = None,
    ) -> PolicyResult:
        result = await self._decide(db, user_id, task_type, spec, approval_token)
        await record(
            db,
            user_id=user_id,
            tool=spec.name,
            decision=result.decision.value,
            approved=None,
            detail={"task_type": task_type, "service": spec.service, "reason": result.reason},
        )
        return result

    async def _decide(self, db, user_id, task_type, spec, approval_token) -> PolicyResult:
        if spec.name not in TASK_TOOLSETS.get(task_type, frozenset()):
            return PolicyResult(Decision.DENY, "이 작업에서 허용되지 않은 도구입니다")
        if spec.grade == Grade.READ:
            return PolicyResult(Decision.ALLOW, "읽기 도구")
        level = await get_level(db, user_id, spec.service)
        if level == PermissionLevel.READ_ONLY:
            return PolicyResult(Decision.DENY, "읽기 전용 서비스입니다")
        if spec.is_payment and approval_token is None:
            return PolicyResult(Decision.NEEDS_APPROVAL, "결제는 항상 승인이 필요합니다")
        if level == PermissionLevel.INSTANT and spec.instant_allowed:
            return PolicyResult(Decision.ALLOW, "즉시 실행 허용")
        if approval_token is None:
            return PolicyResult(Decision.NEEDS_APPROVAL, "사용자 승인이 필요합니다")
        return PolicyResult(Decision.DENY, "승인 서비스 미구현")
