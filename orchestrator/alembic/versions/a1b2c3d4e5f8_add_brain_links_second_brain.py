"""add brain_links table for Second Brain

Revision ID: a1b2c3d4e5f8
Revises: z0t1u2v3w4x5
Create Date: 2026-05-10

"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f8"
down_revision = "g8h9i0j1k2l3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brain_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=True),
        sa.Column("relation", sa.String(200), nullable=True),
        sa.Column("auto_generated", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["knowledge_entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "target_id", name="uq_brain_link_pair"),
    )
    op.create_index("ix_brain_links_user_id", "brain_links", ["user_id"])
    op.create_index("ix_brain_links_source_id", "brain_links", ["source_id"])
    op.create_index("ix_brain_links_target_id", "brain_links", ["target_id"])


def downgrade() -> None:
    op.drop_index("ix_brain_links_target_id", "brain_links")
    op.drop_index("ix_brain_links_source_id", "brain_links")
    op.drop_index("ix_brain_links_user_id", "brain_links")
    op.drop_table("brain_links")
