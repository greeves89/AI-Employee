"""Teams-Termine der Agenten als Besprechungsseite (Tabelle ``meetings``).

Plan docs/plaene/teams-besprechungen.md, Stufe 1.

Revision ID: l0m1n2o3p4q5
Revises: k9l0m1n2o3p4
"""

import sqlalchemy as sa
from alembic import op

revision = "l0m1n2o3p4q5"
down_revision = "k9l0m1n2o3p4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("quelle", sa.String(16), nullable=False, server_default="aufnahme"))
    op.add_column("meetings", sa.Column("agent_id", sa.String(), nullable=True))
    op.add_column("meetings", sa.Column("teams_event_id", sa.String(300), nullable=True))
    op.add_column("meetings", sa.Column("beginn", sa.DateTime(timezone=True), nullable=True))
    op.add_column("meetings", sa.Column("ende", sa.DateTime(timezone=True), nullable=True))
    op.add_column("meetings", sa.Column("status", sa.String(20), nullable=True))
    op.add_column("meetings", sa.Column("gaeste_extern", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("meetings", sa.Column("hinweis", sa.String(20), nullable=True))
    op.add_column("meetings", sa.Column("protokoll", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_meetings_agent_id", "meetings", ["agent_id"])
    op.create_unique_constraint("uq_meetings_agent_event", "meetings", ["agent_id", "teams_event_id"])


def downgrade() -> None:
    op.drop_constraint("uq_meetings_agent_event", "meetings", type_="unique")
    op.drop_index("ix_meetings_agent_id", table_name="meetings")
    for spalte in ("protokoll", "hinweis", "gaeste_extern", "status", "ende", "beginn",
                   "teams_event_id", "agent_id", "quelle"):
        op.drop_column("meetings", spalte)
