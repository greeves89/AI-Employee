"""Add task GC fields: notified, retain, evict_after

Revision ID: q1k2l3m4n5o6
Revises: p0j1k2l3m4n5
Create Date: 2026-04-15

Adds lifecycle GC fields to the tasks table (inspired by Claude Code ch10):
- notified: parent/orchestrator was informed → safe to schedule eviction
- retain: UI is viewing this task → never auto-evict
- evict_after: timestamp after which the task can be purged
"""
from alembic import op
import sqlalchemy as sa

revision = "q1k2l3m4n5o6"
down_revision = "p0j1k2l3m4n5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("notified", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("tasks", sa.Column("retain", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("tasks", sa.Column("evict_after", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "evict_after")
    op.drop_column("tasks", "retain")
    op.drop_column("tasks", "notified")
