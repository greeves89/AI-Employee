"""merge all heads before semantic skill search

Revision ID: c3d4e5f6g7h8
Revises: b1c2d3e4f5g6, q1k2l3m4n5o6, b2v3w4x5y6z7, b2c3d4e5f6g7
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6g7h8"
down_revision = ("b1c2d3e4f5g6", "q1k2l3m4n5o6", "b2v3w4x5y6z7", "b2c3d4e5f6g7")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
