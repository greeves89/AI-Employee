"""Eine Migration darf keine Spalte hart anlegen, die der Startpfad schon anlegt.

Befund #825: ``orchestrator/app/main.py`` legt beim Hochfahren rund 30 Spalten
per ``ALTER TABLE .. ADD COLUMN IF NOT EXISTS`` an (Altlast aus der Zeit vor
den Migrationen). Kommt fuer eine dieser Spalten spaeter eine Revision mit
``op.add_column(...)``, existiert die Spalte auf jeder Anlage, die vorher
einmal gestartet ist -- ``DuplicateColumnError``, und weil Alembic die Kette
der Reihe nach abarbeitet, bleibt ALLES dahinter haengen. Der Orchestrator
kommt dann am naechsten Modell-Feld, das er nicht findet, nicht mehr hoch.

Frische Installationen merken davon nichts (Migration laeuft vor dem
Startpfad). Genau darum faellt es lokal nie auf.

Geprueft wird der Quelltext beider Seiten: die Menge (Tabelle, Spalte) aus
dem Startpfad gegen jedes ``op.add_column("tabelle", sa.Column("spalte", ..))``
in den Revisionen. Eine Ueberschneidung ist ein Fehler; der Weg ist
``op.execute("ALTER TABLE .. ADD COLUMN IF NOT EXISTS ..")`` wie seit #689
Konvention in diesem Baum.

Zweiter Sensor (#834): der SPIEGELFALL. Legt eine Revision eine Tabelle per
rohem ``CREATE TABLE`` an und traegt dabei Spalten, die SQLAlchemy nicht
ausdruecken kann (``vector(n)``, generierte ``tsvector``), laesst das
ORM-Modell sie bewusst weg. Faellt eine Migration auf einer frischen Anlage
aus, entsteht die Tabelle aus dem Modell (``create_all``) — ohne diese Spalten
— und Alembic wird auf HEAD gestempelt: die Revision laeuft nie wieder, der
Mangel ist dauerhaft. Nur der Startpfad kann solche Spalten noch nachtragen.
Deshalb gilt: jede Spalte, die im rohen ``CREATE TABLE`` steht, aber nicht im
ORM-Modell derselben Tabelle, MUSS im Startpfad per
``ADD COLUMN IF NOT EXISTS`` auftauchen. Die erste Wache konnte diese Form
nicht sehen (sie kennt nur ``op.add_column`` und ``ALTER TABLE``), weshalb
``vault_chunks`` durchrutschte.
"""

import ast
import re
import unittest
from pathlib import Path

_ORCH = Path(__file__).resolve().parents[1]
_VERSIONS = _ORCH / "alembic" / "versions"
_MAIN = _ORCH / "app" / "main.py"
# Der Startpfad ist nicht mehr nur main.py: laengere Reparaturen liegen als
# eigenes Modul daneben und werden beim Hochfahren aufgerufen. Kommt eine
# weitere hinzu, gehoert sie HIER hinein -- sonst haelt die Wache eine
# versorgte Spalte faelschlich fuer unversorgt. Die Vorbedingungspruefung
# unten faellt um, wenn eine dieser Dateien wegfaellt oder nichts beitraegt.
_STARTPFAD_QUELLEN = (
    _MAIN,
    _ORCH / "app" / "core" / "vault_chunks_schema.py",
)

_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(", re.IGNORECASE
)
# Erste Worte, die keine Spalte einleiten, sondern eine Tabellenbedingung.
_KEINE_SPALTE = {"constraint", "primary", "unique", "foreign", "check", "exclude", "like"}

_ADD_IF_NOT_EXISTS = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.IGNORECASE
)


def _schleifenwerte(baum: ast.AST) -> dict[int, dict[str, list[str]]]:
    """Je ``for name in ("a", "b")``-Knoten: name -> Literalwerte.

    Der Startpfad legt einige Spalten in kleinen Schleifen ueber Tupel von
    Literalen an (``for _tbl in (...)``, ``for spalte in (...)``) und baut das
    SQL als f-String. Die Werte stehen im Baum -- man muss sie nur einsetzen.
    """
    werte = {}
    for knoten in ast.walk(baum):
        if (isinstance(knoten, ast.For) and isinstance(knoten.target, ast.Name)
                and isinstance(knoten.iter, (ast.Tuple, ast.List))
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                        for e in knoten.iter.elts)):
            werte[id(knoten)] = {knoten.target.id: [e.value for e in knoten.iter.elts]}
    return werte


def _fstring_varianten(knoten: ast.JoinedStr, bindungen: dict[str, list[str]]) -> list[str]:
    """Alle Auspraegungen eines f-Strings unter den bekannten Schleifenwerten.

    Unbekannte Platzhalter werden zu ``?`` -- das matcht kein ``\\w+``, der
    Ausdruck faellt also stumm heraus statt einen falschen Namen zu liefern.
    """
    varianten = [""]
    for teil in knoten.values:
        if isinstance(teil, ast.Constant):
            varianten = [v + str(teil.value) for v in varianten]
        elif (isinstance(teil, ast.FormattedValue) and isinstance(teil.value, ast.Name)
                and teil.value.id in bindungen):
            varianten = [v + w for v in varianten for w in bindungen[teil.value.id]]
        else:
            varianten = [v + "?" for v in varianten]
    return varianten


def _startpfad_spalten(quelle: str) -> set[tuple[str, str]]:
    """(tabelle, spalte) aller ``ADD COLUMN IF NOT EXISTS`` im Startpfad.

    Ueber den AST, nicht zeilenweise: der Startpfad teilt lange SQL-Literale
    ueber implizite String-Verkettung auf zwei Zeilen (``"... IF NOT EXISTS "
    "spalte ..."``) -- als Konstante im Baum ist das wieder EIN String. Und
    f-Strings in Literal-Schleifen (``for _tbl in (...)``) werden ausmultipliziert,
    sonst fehlen acht Spalten (embedding auf drei Tabellen, second_brains.git_*).
    """
    baum = ast.parse(quelle)
    funde = set()

    def _sammle(text: str) -> None:
        for tabelle, spalte in _ADD_IF_NOT_EXISTS.findall(text):
            funde.add((tabelle.lower(), spalte.lower()))

    def _gehe(knoten: ast.AST, bindungen: dict[str, list[str]]) -> None:
        if isinstance(knoten, ast.Constant) and isinstance(knoten.value, str):
            _sammle(knoten.value)
            return
        if isinstance(knoten, ast.JoinedStr):
            for variante in _fstring_varianten(knoten, bindungen):
                _sammle(variante)
            return
        if isinstance(knoten, ast.For) and id(knoten) in schleifen:
            bindungen = {**bindungen, **schleifen[id(knoten)]}
        for kind in ast.iter_child_nodes(knoten):
            _gehe(kind, bindungen)

    schleifen = _schleifenwerte(baum)
    _gehe(baum, {})
    return funde


def _harte_add_columns(quelle: str) -> set[tuple[str, str]]:
    """(tabelle, spalte) jedes ``op.add_column("t", sa.Column("s", ...))`` in ``upgrade()``.

    Nur ``upgrade()``: ein ``downgrade()``, das eine im eigenen ``upgrade()``
    entfernte Startpfad-Spalte zuruecklegt (e286ff01d6fc: embedding), trifft
    auf eine Tabelle OHNE die Spalte -- keine Kollision. Der Startpfad danach
    sagt selbst IF NOT EXISTS.
    """
    funde = set()
    upgrades = [k for k in ast.walk(ast.parse(quelle))
                if isinstance(k, ast.FunctionDef) and k.name == "upgrade"]
    for knoten in (k for fn in upgrades for k in ast.walk(fn)):
        if not (isinstance(knoten, ast.Call)
                and isinstance(knoten.func, ast.Attribute)
                and knoten.func.attr == "add_column"
                and len(knoten.args) >= 2):
            continue
        tabelle, spalte_knoten = knoten.args[0], knoten.args[1]
        if not (isinstance(tabelle, ast.Constant) and isinstance(tabelle.value, str)):
            continue
        if not (isinstance(spalte_knoten, ast.Call) and spalte_knoten.args):
            continue
        spalte = spalte_knoten.args[0]
        if isinstance(spalte, ast.Constant) and isinstance(spalte.value, str):
            funde.add((tabelle.value.lower(), spalte.value.lower()))
    return funde


def _spalten_aus_create_table(text: str) -> dict[str, set[str]]:
    """Tabelle -> Spaltennamen jedes rohen ``CREATE TABLE`` in ``text``.

    Klammern werden gezaehlt, nicht geraten: eine generierte Spalte
    (``GENERATED ALWAYS AS (to_tsvector(...)) STORED``) traegt selbst Klammern
    und Kommata; ein naives ``split(",")`` zerlegt sie in Phantasiespalten.
    """
    gefunden: dict[str, set[str]] = {}
    for treffer in _CREATE_TABLE.finditer(text):
        tabelle = treffer.group(1).lower()
        tiefe, start = 1, treffer.end()
        i = start
        while i < len(text) and tiefe:
            if text[i] == "(":
                tiefe += 1
            elif text[i] == ")":
                tiefe -= 1
            i += 1
        if tiefe:  # unbalanciert -> nichts behaupten
            continue
        rumpf, teil, tiefe2 = text[start:i - 1], "", 0
        teile = []
        for zeichen in rumpf:
            if zeichen == "(":
                tiefe2 += 1
            elif zeichen == ")":
                tiefe2 -= 1
            if zeichen == "," and tiefe2 == 0:
                teile.append(teil)
                teil = ""
            else:
                teil += zeichen
        teile.append(teil)
        spalten = set()
        for eintrag in teile:
            worte = eintrag.split()
            if worte and worte[0].lower() not in _KEINE_SPALTE:
                spalten.add(worte[0].strip('"').lower())
        if spalten:
            gefunden.setdefault(tabelle, set()).update(spalten)
    return gefunden


def _rohe_create_tables(quelle: str) -> dict[str, set[str]]:
    """Wie oben, aber nur aus ``upgrade()`` einer Revision.

    Ein ``downgrade()`` legt nichts an, was der Startpfad tragen muesste.
    """
    gefunden: dict[str, set[str]] = {}
    upgrades = [k for k in ast.walk(ast.parse(quelle))
                if isinstance(k, ast.FunctionDef) and k.name == "upgrade"]
    for knoten in (k for fn in upgrades for k in ast.walk(fn)):
        if isinstance(knoten, ast.Constant) and isinstance(knoten.value, str):
            for tabelle, spalten in _spalten_aus_create_table(knoten.value).items():
                gefunden.setdefault(tabelle, set()).update(spalten)
    return gefunden


def _orm_spalten() -> dict[str, set[str]]:
    """Tabelle -> Spalten, wie ``create_all`` sie anlegen wuerde."""
    from app.models import Base  # noqa: F401 -- registriert alle Modelle

    return {name: {s.name.lower() for s in t.columns}
            for name, t in Base.metadata.tables.items()}


def _startpfad_alle_quellen() -> set[tuple[str, str]]:
    funde: set[tuple[str, str]] = set()
    for pfad in _STARTPFAD_QUELLEN:
        funde |= _startpfad_spalten(pfad.read_text())
    return funde


def _kollisionen(quellen: dict[str, str]) -> list[str]:
    """``name -> Revisionsquelltext`` => Liste der Kollisionen mit dem Startpfad.

    Bewusst EINE Routine fuer die echte Wache und fuer die Gegenprobe: liefe
    die Gegenprobe ueber eigenen Code, koennte die Wache enger werden (z.B.
    wieder nur ``main.py`` lesen) und die Gegenprobe bliebe trotzdem gruen.
    """
    startpfad = _startpfad_alle_quellen()
    return [f"{name}: {tabelle}.{spalte}"
            for name, quelle in sorted(quellen.items())
            for tabelle, spalte in sorted(_harte_add_columns(quelle) & startpfad)]


def _revisionen():
    return sorted(p for p in _VERSIONS.glob("*.py") if p.name != "__init__.py")


class MigrationKollidiertNichtMitStartpfadTests(unittest.TestCase):
    def test_startpfad_liste_ist_nicht_leer(self):
        """Vorbedingung: liest die Wache den Startpfad ueberhaupt? Eine leere
        Menge liesse jeden Kollisionstest gruen durchlaufen."""
        spalten = _startpfad_spalten(_MAIN.read_text())
        self.assertIn(("mcp_servers", "oauth_callback_base_url"), spalten)
        # Die f-String-Schleifen muessen ausmultipliziert sein -- sonst fehlen
        # acht Spalten, und die Wache ist genau dort blind, wo Migrationen fuer
        # Embeddings am haeufigsten nachgezogen werden.
        self.assertIn(("skills", "embedding"), spalten)
        self.assertIn(("agent_memories", "embedding"), spalten)
        self.assertIn(("second_brains", "git_url"), spalten)
        self.assertIn(("second_brains", "git_last_status"), spalten)
        self.assertGreater(len(spalten), 40, spalten)

    def test_keine_revision_legt_startpfad_spalte_hart_an(self):
        # ALLE Startpfad-Quellen, nicht nur main.py: sonst waeren genau die
        # Spalten unbewacht, die eine zweite Datei nachtraegt (#834).
        kollisionen = _kollisionen({p.name: p.read_text() for p in _revisionen()})
        self.assertEqual(
            kollisionen, [],
            "op.add_column fuer eine Spalte, die der Startpfad (app/main.py) schon "
            "per ADD COLUMN IF NOT EXISTS anlegt -> DuplicateColumnError auf jeder "
            "bestehenden Anlage, ganze Kette blockiert (#825). Stattdessen "
            "op.execute(\"ALTER TABLE .. ADD COLUMN IF NOT EXISTS ..\"):\n  "
            + "\n  ".join(kollisionen),
        )

    def test_auch_spalten_der_zweiten_startpfad_quelle_sind_bewacht(self):
        """#834: der Startpfad besteht aus mehr als main.py. Eine Migration,
        die ``vault_chunks.embedding`` hart anlegt, waere auf jeder bereits
        gestarteten Anlage ein DuplicateColumnError -- die Wache muss sie
        finden, obwohl die Spalte NICHT in main.py steht."""
        kuenstlich = {
            "z9_test.py": 'def upgrade() -> None:\n'
                          '    op.add_column("vault_chunks", sa.Column("embedding", sa.String()))\n'
        }
        self.assertEqual(_kollisionen(kuenstlich), ["z9_test.py: vault_chunks.embedding"])
        # Beleg, dass der Treffer WIRKLICH aus der zweiten Quelle kommt:
        self.assertNotIn(("vault_chunks", "embedding"), _startpfad_spalten(_MAIN.read_text()))

    def test_wache_erkennt_den_belegfall(self):
        """Gegenprobe: die Form, die #825 ausgeloest hat, muss gefunden werden --
        sonst prueft die Wache Prosa statt Verhalten."""
        belegfall = (
            'def upgrade() -> None:\n'
            '    op.add_column("mcp_servers", sa.Column("oauth_callback_base_url", sa.Text(), nullable=True))\n'
        )
        self.assertEqual(_harte_add_columns(belegfall), {("mcp_servers", "oauth_callback_base_url")})
        # Und die reparierte Form ist fuer die Wache unsichtbar:
        repariert = (
            'def upgrade() -> None:\n'
            '    op.execute("ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS oauth_callback_base_url TEXT")\n'
        )
        self.assertEqual(_harte_add_columns(repariert), set())
        # Ein downgrade(), das die Spalte zuruecklegt, ist kein Treffer:
        nur_downgrade = (
            'def upgrade() -> None:\n'
            '    op.drop_column("agent_memories", "embedding")\n'
            'def downgrade() -> None:\n'
            '    op.add_column("agent_memories", sa.Column("embedding", sa.NullType()))\n'
        )
        self.assertEqual(_harte_add_columns(nur_downgrade), set())


class RoheCreateTableSpaltenBrauchenDenStartpfadTests(unittest.TestCase):
    """#834: Spalten, die nur im rohen ``CREATE TABLE`` stehen, muessen der
    Startpfad tragen -- ``create_all`` kann sie nicht anlegen."""

    def test_sensor_sieht_ueberhaupt_rohe_create_tables(self):
        """Vorbedingung: findet der Sensor die drei bekannten rohen
        ``CREATE TABLE``? Ohne Treffer liefe der Klassentest leer gruen."""
        tabellen = {}
        for pfad in _revisionen():
            for tabelle, spalten in _rohe_create_tables(pfad.read_text()).items():
                tabellen.setdefault(tabelle, set()).update(spalten)
        self.assertIn("vault_chunks", tabellen)
        self.assertIn("users", tabellen)
        self.assertIn("device_tokens", tabellen)
        # Und die beiden Spalten, um die es geht, werden wirklich gelesen --
        # auch die generierte, die Klammern und ein Komma enthaelt.
        self.assertIn("embedding", tabellen["vault_chunks"])
        self.assertIn("ts", tabellen["vault_chunks"])
        # Keine Phantasiespalte aus dem Inneren des GENERATED-Ausdrucks:
        self.assertNotIn("to_tsvector", tabellen["vault_chunks"])
        self.assertNotIn("coalesce", tabellen["vault_chunks"])

    def test_startpfad_quellen_tragen_alle_bei(self):
        """Vorbedingung: jede gelistete Startpfad-Datei existiert UND liefert
        Spalten. Wird eine verschoben oder umbenannt, faellt das hier auf --
        nicht erst dadurch, dass die Wache eine versorgte Spalte anmahnt."""
        for pfad in _STARTPFAD_QUELLEN:
            self.assertTrue(pfad.exists(), pfad)
            self.assertTrue(_startpfad_spalten(pfad.read_text()), f"keine Spalten in {pfad}")

    def test_jede_nur_im_sql_stehende_spalte_wird_beim_start_nachgetragen(self):
        startpfad = _startpfad_alle_quellen()
        orm = _orm_spalten()
        luecken = []
        for pfad in _revisionen():
            for tabelle, spalten in sorted(_rohe_create_tables(pfad.read_text()).items()):
                if tabelle not in orm:
                    # Das ORM kennt die Tabelle gar nicht -> create_all legt sie
                    # nicht an, es gibt keine stille Teilform.
                    continue
                for spalte in sorted(spalten - orm[tabelle]):
                    if (tabelle, spalte) not in startpfad:
                        luecken.append(f"{pfad.name}: {tabelle}.{spalte}")
        self.assertEqual(
            luecken, [],
            "Spalte steht nur im rohen CREATE TABLE der Migration und fehlt im "
            "ORM-Modell -- faellt eine Migration auf einer frischen Anlage aus, "
            "entsteht die Tabelle ohne sie und Alembic wird gestempelt: dauerhaft "
            "kaputt (#834). Der Startpfad muss sie per ADD COLUMN IF NOT EXISTS "
            "nachtragen:\n  " + "\n  ".join(luecken),
        )

    def test_wache_erkennt_den_belegfall(self):
        """Gegenprobe: genau die Form aus #834 muss auffallen -- und die
        reparierte Form darf es nicht mehr."""
        revision = (
            'def upgrade() -> None:\n'
            '    op.execute("""\n'
            '        CREATE TABLE IF NOT EXISTS vault_chunks (\n'
            '            id BIGSERIAL PRIMARY KEY,\n'
            '            content TEXT NOT NULL,\n'
            '            embedding vector(1024),\n'
            "            ts tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED,\n"
            '            CONSTRAINT uq_x UNIQUE (content)\n'
            '        )\n'
            '    """)\n'
        )
        gelesen = _rohe_create_tables(revision)
        self.assertEqual(gelesen, {"vault_chunks": {"id", "content", "embedding", "ts"}})

        orm_ohne = {"vault_chunks": {"id", "content"}}
        unversorgt = {s for s in gelesen["vault_chunks"] - orm_ohne["vault_chunks"]}
        self.assertEqual(unversorgt, {"embedding", "ts"})

        # Unversorgt gegen einen Startpfad, der nichts davon kennt -> Luecke.
        leerer_startpfad = _startpfad_spalten('x = "ALTER TABLE andere ADD COLUMN IF NOT EXISTS y int"')
        self.assertEqual(
            {s for s in unversorgt if ("vault_chunks", s) not in leerer_startpfad},
            {"embedding", "ts"},
        )
        # Und gegen den ECHTEN Startpfad dieses Baums -> keine Luecke mehr.
        echt = _startpfad_alle_quellen()
        self.assertEqual({s for s in unversorgt if ("vault_chunks", s) not in echt}, set())

    def test_downgrade_create_table_ist_kein_treffer(self):
        nur_downgrade = (
            'def upgrade() -> None:\n'
            '    pass\n'
            'def downgrade() -> None:\n'
            '    op.execute("CREATE TABLE IF NOT EXISTS alt (id serial, weg vector(3))")\n'
        )
        self.assertEqual(_rohe_create_tables(nur_downgrade), {})


if __name__ == "__main__":
    unittest.main()
