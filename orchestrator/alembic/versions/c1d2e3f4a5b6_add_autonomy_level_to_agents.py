"""add autonomy_level to agents

Revision ID: c1d2e3f4a5b6
Revises: b2v3w4x5y6z7
Create Date: 2026-04-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b2v3w4x5y6z7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "autonomy_level",
            sa.String(),
            nullable=False,
            server_default="l3",
        ),
    )


def downgrade() -> None:
    op.drop_column("agents", "autonomy_level")
