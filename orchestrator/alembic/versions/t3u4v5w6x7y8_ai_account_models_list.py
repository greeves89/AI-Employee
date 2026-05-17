"""ai_accounts: model_name -> models list

Revision ID: t3u4v5w6x7y8
Revises: s2t3u4v5w6x7
Create Date: 2026-05-17

An AI account can expose multiple models (for Azure OpenAI: multiple
deployments). The single model_name column becomes a JSON list `models`.
"""
from alembic import op
import sqlalchemy as sa

revision = "t3u4v5w6x7y8"
down_revision = "s2t3u4v5w6x7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_accounts",
        sa.Column("models", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.drop_column("ai_accounts", "model_name")


def downgrade() -> None:
    op.add_column(
        "ai_accounts",
        sa.Column("model_name", sa.String(), nullable=False, server_default=""),
    )
    op.drop_column("ai_accounts", "models")
