"""merge taskforce and gpt55-backfill heads

Revision ID: 0ea61527a17e
Revises: c4d5e6f7a8b9, 515d03f814a0
Create Date: 2026-07-06 12:00:00.000000

Two parallel alembic heads existed, both branched off c3d4e5f6a7b8:
  - c4d5e6f7a8b9 (meeting deliverable / taskforce build mode)
  - 515d03f814a0 (backfill agents.model 'gpt-5-codex' -> 'gpt-5.5', #300)

#300 was authored off the wrong parent, creating a fork. `alembic upgrade
head` became ambiguous ("Multiple head revisions are present"), so the
startup auto-migration silently no-ops. This merge reunites them into a
single linear head. No schema changes here — both parents carry the DDL/data.
"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = '0ea61527a17e'
down_revision: Union[str, Sequence[str], None] = ('c4d5e6f7a8b9', '515d03f814a0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
