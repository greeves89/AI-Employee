"""add user activity tracking and agent auto_stop config

Revision ID: l6f7g8h9i0j1
Revises: k5e6f7g8h9i0
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

revision = "l6f7g8h9i0j1"
down_revision = "k5e6f7g8h9i0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # User activity tracking
    op.add_column(
        "users",
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_last_active_at", "users", ["last_active_at"])


def downgrade() -> None:
    op.drop_index("ix_users_last_active_at", table_name="users")
    op.drop_column("users", "last_active_at")
