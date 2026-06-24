"""Add second_brains table (department-shared knowledge vaults)

Revision ID: w1x2y3z4a5b6
Revises: k1l2m3n4o5p6
Create Date: 2026-06-24

A Second Brain is a DB-managed mount-catalog entry: a shared Markdown vault
under /srv/secondbrain/<slug>/ bind-mounted into assigned agents. All access
control reuses the existing mount-label machinery (user_mount_access +
custom_roles.mount_labels), keyed on the brain's label.
"""
from alembic import op
import sqlalchemy as sa

revision = "w1x2y3z4a5b6"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "second_brains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("host_path", sa.String(), nullable=False),
        sa.Column("container_path", sa.String(), nullable=False),
        sa.Column("default_mode", sa.String(), nullable=False, server_default="rw"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_second_brains_label", "second_brains", ["label"], unique=True)
    op.create_index("ix_second_brains_slug", "second_brains", ["slug"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_second_brains_slug", table_name="second_brains")
    op.drop_index("ix_second_brains_label", table_name="second_brains")
    op.drop_table("second_brains")
