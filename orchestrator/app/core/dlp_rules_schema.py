"""dlp_rules bei jedem Start herstellen und die globalen Standardregeln einmal anlegen (#865).

Die Tabelle entsteht auf einer frischen Anlage NICHT aus dem ``CREATE TABLE IF
NOT EXISTS`` unten, sondern vorher aus ``Base.metadata.create_all`` — also aus
dem Modell. Das Modell kannte ``created_at`` und ``enabled`` nur mit
Python-Standardwerten; ``create_all`` legte beide deshalb als NOT NULL OHNE
Datenbank-Standard an. Das ``CREATE TABLE IF NOT EXISTS`` (mit ``DEFAULT
now()``) war danach ein No-Op, der rohe Seed-INSERT ohne ``created_at`` scheiterte
an der NOT-NULL-Bedingung — bei jedem Start, und ``dlp_rules`` blieb leer:

    Could not ensure dlp_rules table: ... NotNullViolationError:
    null value in column "created_at" of relation "dlp_rules"

Die Filterung selbst fiel still auf ``DEFAULT_ACTIONS`` zurueck; die
Admin-Konsole zeigte aber keine einzige Regel.

Drei Dinge, damit das nicht wieder auseinanderlaeuft:
  1. Die Standardwerte stehen auf bestehenden Anlagen per ``ALTER ... SET
     DEFAULT`` nachtraeglich in der Datenbank (idempotent).
  2. Der Seed-INSERT gibt ``created_at`` selbst mit — er haengt nicht davon ab,
     woher die Tabelle stammt.
  3. Das Modell traegt ``server_default`` (siehe app/models/dlp_rule.py), damit
     ``create_all`` und dieser Startpfad dasselbe Schema erzeugen.
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

ENSURE_STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS dlp_rules ("
    "id serial PRIMARY KEY, pii_class varchar(40) NOT NULL, agent_id varchar,"
    "action varchar(20) NOT NULL, enabled boolean NOT NULL DEFAULT true,"
    "created_at timestamptz NOT NULL DEFAULT now())",
    "CREATE INDEX IF NOT EXISTS ix_dlp_rules_class ON dlp_rules (pii_class)",
    # Reparatur fuer Tabellen aus create_all (Modell ohne server_default).
    "ALTER TABLE dlp_rules ALTER COLUMN created_at SET DEFAULT now()",
    "ALTER TABLE dlp_rules ALTER COLUMN enabled SET DEFAULT true",
)

SEED_STATEMENT = (
    "INSERT INTO dlp_rules (pii_class, agent_id, action, enabled, created_at) "
    "VALUES (:c, NULL, :a, true, now())"
)


async def ensure_dlp_rules_schema(engine) -> int:
    """Stellt die Tabelle her und legt fehlende globale Standardregeln an.

    Gibt die Zahl der neu angelegten Regeln zurueck (0, wenn schon globale
    Regeln da waren). Geseedet wird wie bisher nur, solange es noch KEINE
    globale Regel gibt — was ein Admin geaendert oder geloescht hat, bleibt so.
    """
    from app.core.dlp import DEFAULT_ACTIONS

    async with engine.begin() as conn:
        for statement in ENSURE_STATEMENTS:
            await conn.execute(text(statement))
        existing = (await conn.execute(text(
            "SELECT count(*) FROM dlp_rules WHERE agent_id IS NULL"
        ))).scalar() or 0
        if existing:
            return 0
        for cls, act in DEFAULT_ACTIONS.items():
            await conn.execute(text(SEED_STATEMENT), {"c": cls, "a": act})
    return len(DEFAULT_ACTIONS)
