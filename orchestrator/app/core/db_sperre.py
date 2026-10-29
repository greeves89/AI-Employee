"""Beratende Sperren in PostgreSQL: eine Sache nur von einem gleichzeitig.

Für Token-Erneuerungen mit rotierenden Refresh-Tokens (Microsoft, MCP-Server):
Erneuern zwei Aufrufe gleichzeitig, verbraucht der erste den Refresh-Token, und der
zweite scheitert mit einem bereits ungültigen — die Verbindung gilt dann als
getrennt (#462 für MCP-Server, #1013 für die Integrationen).

``pg_advisory_xact_lock(namensraum, id)`` gilt bis zum Ende der Transaktion
(commit/rollback). Wer die Sperre nimmt, MUSS die Transaktion danach beenden.
Auf anderen Datenbanken (SQLite in den Tests) gibt es keinen Wettlauf zwischen
Prozessen — dort ist die Sperre wirkungslos.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def ist_postgres(db: AsyncSession) -> bool:
    try:
        return db.bind.dialect.name == "postgresql"
    except Exception:  # noqa: BLE001
        return False


@asynccontextmanager
async def transaktionssperre(db: AsyncSession, namensraum: int, schluessel: int):
    """Sperre auf (namensraum, schluessel) bis zum Ende der laufenden Transaktion."""
    if ist_postgres(db):
        await db.execute(text("SELECT pg_advisory_xact_lock(:ns, :oid)"),
                         {"ns": int(namensraum), "oid": int(schluessel)})
    yield
