"""Migrationen muessen sich VOR dem Aufspielen ansehen lassen.

Befund #689: `alembic upgrade heads --sql` erzeugt das SQL, ohne eine Datenbank
anzufassen — der uebliche Weg, eine Migration zu lesen, bevor sie auf eine
Anlage geht. Eine einzige Revision, die dabei eine echte Verbindung braucht,
blockiert die Vorschau fuer ALLE nachfolgenden, weil Alembic die Kette der Reihe
nach abarbeitet.

Betroffen waren sieben Revisionen mit zwei Mustern:

* `SELECT ... ; result.fetchone()` — im Offline-Modus gibt es keine Verbindung,
  an der ein Ergebnis abzuholen waere
* `inspect(bind).get_table_names()` — dasselbe in Gruen

Praktische Folge: „geht in diesem Baum nicht" stand zweimal in einem PR-Text
(#666, #685), und Migrationen gingen ungesehen auf die Anlage. Genau an dem
Punkt wird aus einem Einzelfall eine Gewohnheit.

Geprueft wird der Quelltext der Revisionen — der Lauf selbst haengt in der CI
(Job `migrations-offline`).
"""

import re
import unittest
from pathlib import Path

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_CI = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml").read_text()

#: Aufrufe, die eine echte Verbindung brauchen.
_BRAUCHT_VERBINDUNG = (
    re.compile(r"\.fetchone\(\)"),
    re.compile(r"\.fetchall\(\)"),
    re.compile(r"\.scalar\(\)"),
    re.compile(r"\.first\(\)"),
    re.compile(r"inspect\(\s*(bind|conn|op\.get_bind\(\))"),
)


def _revisionen():
    return sorted(p for p in _VERSIONS.glob("*.py") if p.name != "__init__.py")


def _ohne_kommentare(text: str) -> str:
    """Kommentarzeilen raus, bevor gesucht wird.

    Sonst schlaegt der Test auf seine eigene Begruendung an: die Kommentare in
    den reparierten Revisionen NENNEN `inspect(bind)`, um zu erklaeren, warum es
    dort nicht mehr steht. Ein Test, der Prosa zaehlt statt Verhalten, ist
    genau die Sorte, die spaeter niemand mehr ernst nimmt.
    """
    return "\n".join(z for z in text.splitlines()
                      if not z.lstrip().startswith("#"))


class KeineRevisionBrauchtEineVerbindungTests(unittest.TestCase):
    def test_keine_leseoperation_ohne_offline_ausnahme(self):
        """Wer offline liest, muss den Fall abfangen — sonst reisst er die
        ganze Kette mit."""
        funde = []
        for p in _revisionen():
            text = _ohne_kommentare(p.read_text())
            if not any(m.search(text) for m in _BRAUCHT_VERBINDUNG):
                continue
            # Erlaubt, wenn die Revision den Offline-Modus ausdruecklich abfaengt.
            if "as_sql" in text:
                continue
            funde.append(p.name)
        self.assertEqual(
            funde, [],
            "Diese Revisionen lesen aus der Datenbank, ohne den Offline-Modus "
            "abzufangen — sie blockieren `alembic upgrade heads --sql` fuer die "
            "gesamte nachfolgende Kette:\n  " + "\n  ".join(funde),
        )

    def test_der_datenseed_faengt_den_offline_modus_ab(self):
        """Ein Seed, der seine eigenen Einfuege-IDs zurueckliest, laesst sich
        offline nicht darstellen — er muss aussteigen, nicht scheitern."""
        p = _VERSIONS / "e6f7g8h9i0j1_trading_agent_template.py"
        if not p.exists():
            self.skipTest("Revision nicht vorhanden")
        text = p.read_text()
        self.assertIn("op.get_context().as_sql", text)
        block = text.split("def upgrade()", 1)[1][:900]
        self.assertIn("return", block)

    def test_die_idempotenz_kommt_aus_der_datenbank(self):
        """`ADD COLUMN IF NOT EXISTS` statt „erst lesen, dann anlegen" — das
        erledigt dieselbe Pruefung ohne Verbindung."""
        treffer = sum(1 for p in _revisionen() if "IF NOT EXISTS" in p.read_text())
        self.assertGreaterEqual(treffer, 4)


class DieVorschauHaengtInDerCiTests(unittest.TestCase):
    def test_es_gibt_einen_job(self):
        self.assertIn("migrations-offline:", _CI)

    def test_er_ruft_den_offline_modus_auf(self):
        self.assertIn("alembic upgrade heads --sql", _CI)

    def test_er_prueft_dass_wirklich_etwas_erzeugt_wurde(self):
        """Ein leeres Ergebnis ist kein Erfolg — der Lauf koennte abgebrochen
        sein, ohne einen Fehlercode zu setzen."""
        block = _CI.split("migrations-offline:", 1)[1].split("\n  release-track:", 1)[0]
        self.assertIn("-gt 100", block)

    def test_er_braucht_keine_datenbank(self):
        """Das ist der Sinn des Offline-Modus — mit Datenbank waere es ein
        anderer, langsamerer Test."""
        block = _CI.split("migrations-offline:", 1)[1].split("\n  release-track:", 1)[0]
        self.assertNotIn("services:", block)
        self.assertNotIn("postgres", block.lower())


if __name__ == "__main__":
    unittest.main()
