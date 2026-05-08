"""publish_builtin_templates — set is_published=true for all builtin templates

Revision ID: g8h9i0j1k2l3
Revises: f7g8h9i0j1k2
Create Date: 2026-05-08

"""
from alembic import op
from sqlalchemy.sql import text

revision = "g8h9i0j1k2l3"
down_revision = "f7g8h9i0j1k2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(
        "UPDATE agent_templates SET is_published = true WHERE is_builtin = true AND is_published = false"
    ))


def downgrade() -> None:
    pass
