"""custom_roles table + users.custom_role_id

Revision ID: c3d4e5f6g7h9
Revises: b2c3d4e5f6g7
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "p1b2b2b2b2b2"
down_revision = "p1a1a1a1a1a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("permissions", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_custom_roles_name"),
    )
    op.create_index("ix_custom_roles_name", "custom_roles", ["name"])
    op.add_column(
        "users",
        sa.Column("custom_role_id", sa.Integer(), sa.ForeignKey("custom_roles.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "custom_role_id")
    op.drop_index("ix_custom_roles_name", table_name="custom_roles")
    op.drop_table("custom_roles")
