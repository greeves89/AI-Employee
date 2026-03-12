"""merge heads

Revision ID: 3bbdf0564184
Revises: b5f7e8a9c0d1, e5f6a7b8c9d0
Create Date: 2026-03-12 08:01:34.430924

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3bbdf0564184'
down_revision: Union[str, None] = ('b5f7e8a9c0d1', 'e5f6a7b8c9d0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
