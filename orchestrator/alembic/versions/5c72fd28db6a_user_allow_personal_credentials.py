"""Einzelfreigabe eigener KI-Abos je Nutzer (allow_personal_credentials)

Revision ID: 5c72fd28db6a
Revises: w8x9y0z1a2b3
Create Date: 2026-08-18

Nachzieh-Migration zu 899513e (v1.238.0): das Modell bekam die Spalte
`users.allow_personal_credentials`, die Migration dazu fehlte. Ohne sie
schlaegt jede ORM-Abfrage auf `users` mit UndefinedColumnError fehl, sobald
der Code mit der neuen Modell-Spalte laeuft. Rein additiv, IF NOT EXISTS
analog zu w8x9y0z1a2b3 — frisch provisionierte Datenbanken bekommen die
Spalte bereits ueber create_all.
"""
from alembic import op

revision = "5c72fd28db6a"
down_revision = "w8x9y0z1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "allow_personal_credentials BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS allow_personal_credentials")
