"""meeting room stages config

Revision ID: y9s0t1u2v3w4
Revises: j4d5e6f7g8h9
Create Date: 2026-04-20

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "y9s0t1u2v3w4"
down_revision = "x8r9s0t1u2v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meeting_rooms",
        sa.Column("stages_config", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meeting_rooms", "stages_config")
