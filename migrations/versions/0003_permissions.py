"""서비스별 권한 수준

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_permissions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("service", sa.Text, nullable=False),
        sa.Column("level", sa.Text, nullable=False),
        sa.UniqueConstraint("user_id", "service"),
    )


def downgrade() -> None:
    op.drop_table("service_permissions")
