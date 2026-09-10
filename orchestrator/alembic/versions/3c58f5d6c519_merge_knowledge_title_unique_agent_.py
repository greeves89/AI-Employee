"""merge knowledge title, agent favorite and MCP OAuth callback heads

Revision ID: 3c58f5d6c519
Revises: b7c1e93a5f20, a1g2e3n4t5f6, g5h6i7j8k9l0
Create Date: 2026-09-10 07:09:56.058891

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = '3c58f5d6c519'
down_revision: Union[str, Sequence[str], None] = (
    'b7c1e93a5f20', 'a1g2e3n4t5f6', 'g5h6i7j8k9l0',
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
