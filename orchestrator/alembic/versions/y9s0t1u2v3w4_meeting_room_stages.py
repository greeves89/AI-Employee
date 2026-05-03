"""meeting room stages config

Revision ID: z1t2u3v4w5x6
Revises: z0t1u2v3w4x5
Create Date: 2026-04-20

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "z1t2u3v4w5x6"
down_revision = "z0t1u2v3w4x5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meeting_rooms",
        sa.Column("stages_config", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meeting_rooms", "stages_config")
