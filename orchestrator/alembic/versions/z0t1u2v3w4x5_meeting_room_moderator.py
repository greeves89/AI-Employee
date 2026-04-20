"""meeting room moderator flag

Revision ID: z0t1u2v3w4x5
Revises: y9s0t1u2v3w4
Create Date: 2026-04-20

"""
from alembic import op
import sqlalchemy as sa

revision = "z0t1u2v3w4x5"
down_revision = "y9s0t1u2v3w4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meeting_rooms",
        sa.Column("use_moderator", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("meeting_rooms", "use_moderator")
