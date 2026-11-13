"""Objekt-ID der Person im Firmen-IdP am Konto (MCP per OAuth, #1195).

Revision ID: o3p4q5r6s7t8
Revises: l0m1n2o3p4q5
"""

import sqlalchemy as sa
from alembic import op

revision = "o3p4q5r6s7t8"
down_revision = "l0m1n2o3p4q5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("idp_object_id", sa.String(), nullable=True))
    op.create_index("ix_users_idp_object_id", "users", ["idp_object_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_idp_object_id", table_name="users")
    op.drop_column("users", "idp_object_id")
