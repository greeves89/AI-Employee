"""add webhook_token_hash to agents for external webhook triggers

Revision ID: b6f263e51643
Revises: 975ce832c37b
Create Date: 2026-04-15 18:37:32.276325

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6f263e51643'
down_revision: Union[str, None] = '975ce832c37b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SHA-256 hash of the plaintext webhook token. The plaintext is shown
    # to the user exactly once (at creation/rotation) and is never stored.
    op.add_column(
        "agents",
        sa.Column("webhook_token_hash", sa.String(length=64), nullable=True),
    )
    # Partial index only on rows where the hash is set (lookup path).
    op.create_index(
        "ix_agents_webhook_token_hash",
        "agents",
        ["webhook_token_hash"],
        unique=False,
        postgresql_where=sa.text("webhook_token_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agents_webhook_token_hash", table_name="agents")
    op.drop_column("agents", "webhook_token_hash")
