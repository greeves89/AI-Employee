"""Anbieter des Projekt-Repos (``teams.repo_anbieter``): GitLab neben GitHub und Forgejo (#1194).

Revision ID: m2gitlab1194
Revises: l0m1n2o3p4q5
"""

import sqlalchemy as sa
from alembic import op

revision = "m2gitlab1194"
down_revision = "l0m1n2o3p4q5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("repo_anbieter", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("teams", "repo_anbieter")
