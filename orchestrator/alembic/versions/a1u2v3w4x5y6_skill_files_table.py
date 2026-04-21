"""add skill_files table

Revision ID: a1u2v3w4x5y6
Revises: z0t1u2v3w4x5
Create Date: 2026-04-20

"""
from alembic import op
import sqlalchemy as sa

revision = "a1u2v3w4x5y6"
down_revision = "z0t1u2v3w4x5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_files",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False, server_default="application/octet-stream"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_skill_files_skill_id", "skill_files", ["skill_id"])
    op.create_index("ix_skill_files_created_at", "skill_files", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_skill_files_created_at", table_name="skill_files")
    op.drop_index("ix_skill_files_skill_id", table_name="skill_files")
    op.drop_table("skill_files")
