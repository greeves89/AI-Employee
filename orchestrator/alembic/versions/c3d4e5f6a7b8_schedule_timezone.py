"""Add timezone to schedules (DST-aware cron evaluation)

Revision ID: c3d4e5f6a7b8
Revises: c1d2e3f4a5b7
Create Date: 2026-07-02

Adds an IANA timezone column to the schedules table. When a
cron_expression is set, next_run_at is computed in this zone so that
"0 6 * * *" fires at 06:00 wall-clock time (DST-aware) rather than
06:00 UTC. Existing rows default to "UTC" → 100% backwards-compatible
(cron was previously evaluated in UTC).
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "c1d2e3f4a5b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schedules",
        sa.Column("timezone", sa.String(), nullable=False, server_default="UTC"),
    )


def downgrade() -> None:
    op.drop_column("schedules", "timezone")
