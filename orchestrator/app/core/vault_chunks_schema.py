"""Startpfad-Reparatur fuer ``vault_chunks`` (#834).

``vault_chunks`` traegt zwei Spalten, die SQLAlchemy nicht ausdruecken kann und
die das ORM-Modell deshalb bewusst weglaesst: ``embedding vector(1024)``
(pgvector) und ``ts`` (generierte ``tsvector``-Spalte). Sie entstehen
ausschliesslich im rohen ``CREATE TABLE`` der Revision
``v7h1b2r3d4s5_vault_chunks_hybrid_search``.

Faellt ``alembic upgrade head`` auf einer FRISCHEN Datenbank aus, legt der
Rueckfall die Tabelle per ``create_all`` aus dem Modell an — ohne beide Spalten
und ohne ``DEFAULT now()`` auf ``updated_at`` — und stempelt Alembic auf HEAD.
Ab da ist der Zustand dauerhaft: die Revision gilt als angewandt und laeuft nie
wieder, und BEIDE Einfuegewege des Indexers scheitern (mit Vektor an der
fehlenden Spalte, ohne Vektor am ``NOT NULL`` ohne Vorgabewert). Der
Gesetze-Crawler indiziert daraufhin endlos dieselben Dateien neu, weil die
Ueberspringen-Abfrage auf einer immer leeren Tabelle nie trifft — gemeldet als
ein dauerhaft ausgelasteter Kern ohne eine einzige Zeile in der Tabelle.

Warum hier und nicht in der Reparaturschleife von ``_init_db_from_models``:
jene Funktion laeuft NUR, wenn eine Migration scheitert. Auf einer bereits
beschaedigten Anlage ist alles gestempelt, ``upgrade head`` gelingt also — und
eine Reparatur dort wuerde nie wieder angefasst. Diese hier laeuft bei JEDEM
Start und heilt deshalb auch den Bestand.

Jede Anweisung ist fuer sich idempotent und laeuft in einer EIGENEN Transaktion:
scheitert eine (fehlende pgvector-Erweiterung, zu altes Postgres), laufen die
uebrigen trotzdem. Sonst verlaengert ein fehlender HNSW-Index die Stoerung um
die Volltextsuche, die voellig unabhaengig davon funktioniert.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Reihenfolge ist Absicht: Erweiterung vor Tabelle, Spalte vor ihrem Index.
ENSURE_STATEMENTS: tuple[str, ...] = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    # Deckt den Fall ab, dass die Tabelle ganz fehlt. OHNE die Vektorspalte:
    # sie ist die einzige, die die Erweiterung braucht. Stuende sie mit drin,
    # scheitert bei fehlendem pgvector die GANZE Anweisung, die Tabelle
    # entsteht nicht, und alle folgenden laufen ins Leere -- die Volltextsuche
    # (ts + GIN), die voellig ohne Vektoren auskommt, waere mit begraben.
    # Die Spalte kommt gleich darauf per eigener Anweisung dazu.
    """
    CREATE TABLE IF NOT EXISTS vault_chunks (
        id           BIGSERIAL PRIMARY KEY,
        brain_label  VARCHAR NOT NULL,
        path         VARCHAR NOT NULL,
        chunk_idx    INTEGER NOT NULL,
        heading      TEXT,
        content      TEXT NOT NULL,
        file_hash    VARCHAR NOT NULL,
        ts           tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED,
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_vault_chunk UNIQUE (brain_label, path, chunk_idx)
    )
    """,
    "ALTER TABLE vault_chunks ADD COLUMN IF NOT EXISTS embedding vector(1024)",
    # Eine generierte Spalte darf ADD COLUMN IF NOT EXISTS tragen (PostgreSQL
    # column_constraint); existiert sie schon, passiert nichts. Wird sie
    # wirklich angelegt, schreibt Postgres die Tabelle einmal um. Auf der
    # heute gemeldeten Anlage ist sie leer (jedes Einfuegen scheiterte) -- das
    # gilt aber NICHT mehr, sobald der vektorlose Weg wieder traegt. Genau
    # dagegen steht die Zeitgrenze unten.
    "ALTER TABLE vault_chunks ADD COLUMN IF NOT EXISTS ts tsvector "
    "GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED",
    # create_all traegt den Python-seitigen Vorgabewert des Modells nicht in die
    # Datenbank — der rohe INSERT des Indexers laeuft sonst ins NOT NULL.
    "ALTER TABLE vault_chunks ALTER COLUMN updated_at SET DEFAULT now()",
    "CREATE INDEX IF NOT EXISTS ix_vault_chunks_brain_path ON vault_chunks (brain_label, path)",
    "CREATE INDEX IF NOT EXISTS ix_vault_chunks_ts ON vault_chunks USING gin (ts)",
    "CREATE INDEX IF NOT EXISTS ix_vault_chunks_embedding "
    "ON vault_chunks USING hnsw (embedding vector_cosine_ops)",
)


# Jede Transaktion bekommt diese Grenzen VORAB. Ohne sie wartet der Hochlauf
# unbegrenzt: ``ALTER TABLE`` nimmt den ACCESS-EXCLUSIVE-Lock, BEVOR es merkt,
# dass dank IF NOT EXISTS nichts zu tun ist. Haelt irgendeine Verbindung gerade
# eine Transaktion auf ``vault_chunks`` (laufende Suche, der auslaufende alte
# Behaelter beim Neubau), haengt nicht nur der Start -- hinter dem wartenden
# Lock reiht sich JEDE weitere Abfrage auf die Tabelle ein. Aus einer Reparatur
# wuerde so ein Totalausfall, schlechter zu finden als der Mangel selbst.
# Lieber sauber scheitern und melden: der naechste Start versucht es erneut.
# Teilstrings, ueber die die Sperre unten die beiden Anweisungen wiedererkennt.
# Als Konstante, damit ein Umformulieren des SQL die Sperre nicht still loest --
# ein Test haelt beide gegen ENSURE_STATEMENTS.
_TS_SPALTE = "ADD COLUMN IF NOT EXISTS ts"
_SCHREIBFREIGABE = "ALTER COLUMN updated_at SET DEFAULT"

_ZEITGRENZEN = ("SET LOCAL lock_timeout = '5s'", "SET LOCAL statement_timeout = '120s'")


async def ensure_vault_chunks_schema(engine) -> list[str]:
    """Jede Anweisung einzeln ausfuehren. Gibt die gescheiterten zurueck.

    Der Rueckgabewert ist die Pruefgroesse: leer heisst, das Schema steht
    vollstaendig. Ein Fehler wird gemeldet, bricht die Reihe aber nicht ab.
    """
    from sqlalchemy import text as _txt

    gescheitert: list[str] = []
    ts_steht = True
    for anweisung in ENSURE_STATEMENTS:
        if _SCHREIBFREIGABE in anweisung and not ts_steht:
            # Reihenfolge ist hier eine SPERRE, keine Kosmetik: diese Anweisung
            # allein macht den vektorlosen Einfuegeweg wieder gangbar, die
            # Tabelle fuellt sich also ab hier. Fehlt ``ts`` noch, braucht das
            # spaetere ``ADD COLUMN .. GENERATED`` einen vollen Table-Rewrite
            # unter ACCESS EXCLUSIVE -- auf schwacher Anlage gegen
            # statement_timeout womoeglich nie mehr zu schaffen. Aus einem
            # reparierbaren Zustand wuerde ein dauerhafter, und zwar wieder ein
            # stiller (Volltextsuche fehlt, sonst laeuft alles). Also lieber
            # nicht freigeben und laut bleiben; der naechste Start versucht es.
            kurz = " ".join(anweisung.split())[:80]
            gescheitert.append(kurz)
            logger.warning(
                "vault_chunks-Schema: '%s...' ausgelassen, weil die ts-Spalte fehlt", kurz
            )
            continue
        try:
            async with engine.begin() as conn:
                for grenze in _ZEITGRENZEN:
                    await conn.execute(_txt(grenze))
                await conn.execute(_txt(anweisung))
        except Exception as e:  # noqa: BLE001 — eine Anlage darf daran nicht haengen
            kurz = " ".join(anweisung.split())[:80]
            if _TS_SPALTE in anweisung:
                ts_steht = False
            gescheitert.append(kurz)
            logger.warning("vault_chunks-Schema: '%s...' fehlgeschlagen: %s", kurz, e)
    if gescheitert:
        # Der ZUSTAND "Schema bleibt unvollstaendig" ist der eigentliche Befund --
        # die Einzelwarnungen oben sagen nur, WAS flog. Ohne diese Zeile waere die
        # Reparatur im Fehlerfall selbst wieder so still wie der Mangel (#834).
        # Sie steht HIER und nicht beim Aufrufer, damit ein Test sie erreicht.
        logger.error(
            "vault_chunks bleibt unvollstaendig (%d von %d Anweisungen gescheitert): %s",
            len(gescheitert), len(ENSURE_STATEMENTS), gescheitert,
        )
    else:
        logger.info("vault_chunks schema ensured (embedding + ts + updated_at default + indexes)")
    return gescheitert
