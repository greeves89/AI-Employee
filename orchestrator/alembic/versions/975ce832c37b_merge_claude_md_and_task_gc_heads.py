"""merge claude_md and task_gc heads

Revision ID: 975ce832c37b
Revises: b1b4f9d7981f, q1k2l3m4n5o6
Create Date: 2026-04-15 20:27:00.000000

Two parallel alembic heads existed:
  - b1b4f9d7981f (add claude_md to agent_templates)
  - q1k2l3m4n5o6 (task GC: notified, retain, evict_after columns)

This migration merges them into a single linear history. No schema
changes here — both parent migrations carry the actual DDL.
"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = '975ce832c37b'
down_revision: Union[str, Sequence[str], None] = ('b1b4f9d7981f', 'q1k2l3m4n5o6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
