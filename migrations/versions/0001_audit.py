"""감사 로그 (트리거 기준은 AUDIT_RETENTION과 같은 1095일)

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_refs",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_ref", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("actor_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("tool", sa.Text, nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("approved", sa.Boolean, nullable=True),
        sa.Column("detail", postgresql.JSONB, nullable=False),
    )
    op.create_index("ix_audit_log_actor_ref", "audit_log", ["actor_ref"])
    op.execute(
        """
        CREATE FUNCTION audit_log_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION '감사 로그는 수정할 수 없습니다';
            END IF;
            IF OLD.created_at >= now() - interval '1095 days' THEN
                RAISE EXCEPTION '보관 기간(3년, 1095일) 내 감사 로그는 삭제할 수 없습니다';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER audit_log_guard BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_log_guard ON audit_log")
    op.execute("DROP FUNCTION audit_log_guard()")
    op.drop_table("audit_log")
    op.drop_table("audit_refs")
