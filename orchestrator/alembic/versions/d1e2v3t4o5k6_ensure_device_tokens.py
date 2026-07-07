"""ensure device_tokens table exists (self-heal a stamped-ahead DB)

The original ``apns1dev2token3`` migration creates ``device_tokens``, but on some
deployments the DB was stamped at head without that table ever being created
(branch/merge tangle or a manual ``alembic stamp``), causing 500s on the push-
notification endpoints (``relation "device_tokens" does not exist``). This
migration re-creates the table idempotently off the current merge head.

Revision ID: d1e2v3t4o5k6
Revises: 0ea61527a17e
Create Date: 2026-07-07

"""
from alembic import op
import sqlalchemy as sa

revision = "d1e2v3t4o5k6"
down_revision = "0ea61527a17e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "device_tokens" in insp.get_table_names():
        return  # already present (e.g. Pi) — nothing to do
    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False, server_default="ios"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_device_tokens_user_id", "device_tokens", ["user_id"])
    op.create_index("ix_device_tokens_token", "device_tokens", ["token"], unique=True)


def downgrade() -> None:
    # Non-destructive self-heal — leave the table in place on downgrade.
    pass
