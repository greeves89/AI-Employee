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
"""

import ast
import re
import unittest
from pathlib import Path

_ORCH = Path(__file__).resolve().parents[1]
_VERSIONS = _ORCH / "alembic" / "versions"
_MAIN = _ORCH / "app" / "main.py"

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
        startpfad = _startpfad_spalten(_MAIN.read_text())
        kollisionen = []
        for pfad in _revisionen():
            for tabelle, spalte in sorted(_harte_add_columns(pfad.read_text()) & startpfad):
                kollisionen.append(f"{pfad.name}: {tabelle}.{spalte}")
        self.assertEqual(
            kollisionen, [],
            "op.add_column fuer eine Spalte, die der Startpfad (app/main.py) schon "
            "per ADD COLUMN IF NOT EXISTS anlegt -> DuplicateColumnError auf jeder "
            "bestehenden Anlage, ganze Kette blockiert (#825). Stattdessen "
            "op.execute(\"ALTER TABLE .. ADD COLUMN IF NOT EXISTS ..\"):\n  "
            + "\n  ".join(kollisionen),
        )

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


if __name__ == "__main__":
    unittest.main()
