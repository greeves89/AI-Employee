"""Add improvement-proposal review fields to skills.

Revision ID: e1s2k3i4l5l6
Revises: d1r2o3p4l5o6
Create Date: 2026-05-17
"""

import sqlalchemy as sa
from alembic import op

revision = "e1s2k3i4l5l6"
down_revision = "d1r2o3p4l5o6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("skills") as batch:
        batch.add_column(sa.Column("improvement_proposal", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("improvement_proposed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("improvement_review_reason", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("skills") as batch:
        batch.drop_column("improvement_review_reason")
        batch.drop_column("improvement_proposed_at")
        batch.drop_column("improvement_proposal")
