"""template build tools

Revision ID: 6b2c0bb45b3a
Revises: 4259933782af
Create Date: 2026-09-29

Vorlagen koennen ihren Agenten Build-Werkzeuge fuer Windows-Programme (.exe)
mitgeben: ein eigenes Volume fuer Go, Rust, .NET und llvm-mingw plus die
passende Anleitung. Bestehende Vorlagen bleiben aus; die mitgelieferte
„Fullstack Developer"-Vorlage schaltet der Start-Abgleich ein.
"""
from alembic import op
import sqlalchemy as sa

revision = "6b2c0bb45b3a"
down_revision = "4259933782af"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_templates",
        sa.Column("build_tools", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("agent_templates", "build_tools")
