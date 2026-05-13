"""user_mount_access: per-user RO/RW grants on AGENT_MOUNT_CATALOG labels

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f8
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa

revision = "p1a1a1a1a1a1"
down_revision = ("a1b2c3d4e5f8", "c3d4e5f6g7h8")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_mount_access",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mount_label", sa.String(), nullable=False),
        sa.Column("mode", sa.String(length=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "mount_label", name="uq_user_mount_access_user_label"),
    )
    op.create_index("ix_user_mount_access_user_id", "user_mount_access", ["user_id"])
    op.create_index("ix_user_mount_access_mount_label", "user_mount_access", ["mount_label"])


def downgrade() -> None:
    op.drop_index("ix_user_mount_access_mount_label", table_name="user_mount_access")
    op.drop_index("ix_user_mount_access_user_id", table_name="user_mount_access")
    op.drop_table("user_mount_access")
