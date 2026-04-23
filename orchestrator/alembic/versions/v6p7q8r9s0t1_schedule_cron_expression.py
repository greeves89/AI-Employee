"""Add cron_expression to schedules + make interval_seconds nullable

Revision ID: v6p7q8r9s0t1
Revises: t4n5o6p7q8r9
Create Date: 2026-04-18

Adds an optional cron_expression column to the schedules table.
When set, the scheduler uses it instead of interval_seconds to
calculate next_run_at (e.g. "0 9 * * 1" = every Monday at 09:00).
interval_seconds defaults to 0 and is ignored when cron_expression is set.
"""
from alembic import op
import sqlalchemy as sa

revision = "v6p7q8r9s0t1"
down_revision = "t4n5o6p7q8r9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schedules",
        sa.Column("cron_expression", sa.String(), nullable=True),
    )
    # Allow interval_seconds to be 0 (used when cron_expression drives scheduling)
    op.alter_column("schedules", "interval_seconds", server_default="0")


def downgrade() -> None:
    op.drop_column("schedules", "cron_expression")
