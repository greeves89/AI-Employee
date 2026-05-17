"""Add ai_accounts table + agents.ai_account_id

Revision ID: s2t3u4v5w6x7
Revises: r1s2t3u4v5w6
Create Date: 2026-05-17

Reusable, admin-managed AI model accounts. An agent can reference one via
agents.ai_account_id instead of carrying an inline llm_config.
"""
from alembic import op
import sqlalchemy as sa

revision = "s2t3u4v5w6x7"
down_revision = "r1s2t3u4v5w6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("provider_type", sa.String(), nullable=False),
        sa.Column("api_endpoint", sa.String(), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_accounts_name", "ai_accounts", ["name"], unique=True)
    op.add_column(
        "agents",
        sa.Column("ai_account_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_agents_ai_account_id", "agents", ["ai_account_id"])
    op.create_foreign_key(
        "fk_agents_ai_account_id", "agents", "ai_accounts",
        ["ai_account_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_agents_ai_account_id", "agents", type_="foreignkey")
    op.drop_index("ix_agents_ai_account_id", table_name="agents")
    op.drop_column("agents", "ai_account_id")
    op.drop_index("ix_ai_accounts_name", table_name="ai_accounts")
    op.drop_table("ai_accounts")
