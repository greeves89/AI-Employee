"""Add event_triggers table for webhook-to-task routing rules

Revision ID: r2l3m4n5o6p7
Revises: q1k2l3m4n5o6
Create Date: 2026-04-16

EventTriggers define conditional rules: when a webhook arrives matching
certain conditions (source, event_type, payload fields), create a task
with an interpolated prompt template.
"""
from alembic import op
import sqlalchemy as sa

revision = "r2l3m4n5o6p7"
down_revision = "d4c3daacbef0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_triggers",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False, index=True),
        sa.Column("source_filter", sa.String(), nullable=True),
        sa.Column("event_type_filter", sa.String(), nullable=True),
        sa.Column("payload_conditions", sa.JSON(), nullable=True),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="5"),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="true"),
        sa.Column("fire_count", sa.Integer(), server_default="0"),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("event_triggers")
