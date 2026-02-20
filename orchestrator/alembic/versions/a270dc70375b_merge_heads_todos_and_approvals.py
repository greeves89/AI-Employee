"""merge heads - todos and approvals

Revision ID: a270dc70375b
Revises: 41d29f2ad811, d4e5f6a7b8c9
Create Date: 2026-02-20 09:02:18.861188

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a270dc70375b'
down_revision: Union[str, None] = ('41d29f2ad811', 'd4e5f6a7b8c9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
