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

import ast
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




class GenauEinKopfTests(unittest.TestCase):
    """Die Revisionskette darf nur EINEN Kopf haben — sonst bleiben Migrationen liegen.

    Die Luecke, die dieser Test schliesst, ist eine, die die eigene CI nicht
    sehen kann, weil beide Seiten dasselbe Wort unterschiedlich beugen:

    * Der CI-Job „Migrations-Vorschau" fuehrt ``alembic upgrade heads``
      (MEHRZAHL, ci.yml) — mehrere Koepfe sind dort ausdruecklich erlaubt.
    * Der Start der Anlage fuehrt ``alembic upgrade head`` (EINZAHL,
      ``app/main.py``) — und bricht bei zwei Koepfen mit „Multiple head
      revisions are present" ab. Der Start selbst geht weiter: der Rueckfall
      in ``_migrate_or_fallback`` haelt die Anlage oben, laesst die
      ausstehenden Migrationen aber LIEGEN. Auf einer versorgten Datenbank
      heisst das: die neuen Spalten fehlen, laut und wiederholt, ohne dass
      jemand einen Startabbruch sieht.

    Praktische Folge: Zwei PRs, die je eine Migration mitbringen, sind EINZELN
    gruen und ZUSAMMEN kaputt. Gemerkt hat es bisher nur, wer die Koepfe von
    Hand nachgezaehlt hat — zweimal knapp vor dem Merge, einmal erst beim
    Rebase, als ein fremder PR eine Migration dazwischengeschoben hatte.

    Gezaehlt wird am Quelltext (``ast``), nicht per Regex und ohne Datenbank:
    Merge-Revisionen schreiben ``down_revision`` als mehrzeiliges Tupel, und
    ein Teil der Revisionen schreibt ``revision: str = "..."`` mit Typangabe
    (``ast.AnnAssign``) statt als einfache Zuweisung. Wer nur ``ast.Assign``
    liest, zaehlt genau diese Dateien nicht mit und erfindet sich dadurch
    zusaetzliche Koepfe — ein Fehlalarm, der teurer ist als kein Test.
    """

    @staticmethod
    def _namen(knoten):
        if isinstance(knoten, ast.Assign):
            return [z.id for z in knoten.targets if isinstance(z, ast.Name)]
        if isinstance(knoten, ast.AnnAssign) and isinstance(knoten.target, ast.Name):
            return [knoten.target.id]
        return []

    @staticmethod
    def _werte(knoten):
        wert = knoten.value
        if wert is None:
            return []
        if isinstance(wert, ast.Constant):
            return [wert.value] if wert.value else []
        if isinstance(wert, (ast.Tuple, ast.List)):
            return [e.value for e in wert.elts
                    if isinstance(e, ast.Constant) and e.value]
        return []

    def _kette(self):
        """``{revision: [eltern]}`` ueber alle Revisionsdateien."""
        kette, ohne = {}, []
        for pfad in _revisionen():
            baum = ast.parse(pfad.read_text(encoding="utf-8"))
            revision, eltern = None, []
            for knoten in baum.body:
                namen = self._namen(knoten)
                if "revision" in namen:
                    werte = self._werte(knoten)
                    revision = werte[0] if werte else None
                if "down_revision" in namen:
                    eltern = self._werte(knoten)
            if revision is None:
                ohne.append(pfad.name)
            else:
                kette[revision] = eltern
        return kette, ohne

    def test_jede_revisionsdatei_nennt_ihre_revision(self):
        """Vorbedingung des Kopf-Tests: Wird eine Datei nicht gelesen, fehlt
        sie als Elternteil und der Kopf-Test schlaegt grundlos an."""
        kette, ohne = self._kette()
        self.assertEqual([], ohne, f"Revision nicht erkannt in: {ohne}")
        self.assertEqual(len(kette), len(_revisionen()),
                         "Zwei Dateien tragen dieselbe Revisionskennung")

    def test_die_kette_hat_genau_einen_kopf(self):
        kette, _ = self._kette()
        eltern = {e for liste in kette.values() for e in liste}
        koepfe = sorted(r for r in kette if r not in eltern)
        self.assertEqual(
            1, len(koepfe),
            "Die Revisionskette hat {} Koepfe: {}. `alembic upgrade head` "
            "(Einzahl, beim Start der Anlage) bricht damit ab. Entweder "
            "`down_revision` der neuen Revision auf den Kopf der Hauptlinie "
            "ziehen oder eine Merge-Revision anlegen.".format(len(koepfe), koepfe),
        )

    def test_jedes_genannte_elternteil_existiert(self):
        """Ein Tippfehler in ``down_revision`` erzeugt sonst leise einen
        zweiten Kopf UND eine Revision, die Alembic nie erreicht."""
        kette, _ = self._kette()
        fehlend = sorted({e for liste in kette.values() for e in liste}
                         - set(kette))
        self.assertEqual([], fehlend,
                         f"down_revision zeigt auf unbekannte Revisionen: {fehlend}")


if __name__ == "__main__":
    unittest.main()
