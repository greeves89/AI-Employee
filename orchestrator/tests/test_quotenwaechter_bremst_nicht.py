"""Der Speicherquoten-Waechter darf die Anlage nicht ausbremsen.

Am 25.09.2026 gemessen, waehrend ein Nutzer auf die Antwort seines Agenten
wartete:

    PID      %CPU
    1175666  97.2%  du -sm --exclude=.cache
    1171948  16.2%  npm install @openai/codex@latest
    1164104  14.0%  uvicorn app.main

Der groesste CPU-Verbraucher auf der Anlage war die Gesundheitspruefung
selbst. ``du`` laeuft den ganzen Baum ab; auf einem gewachsenen
Arbeitsbereich (git-Auschecken, node_modules, venvs) sind das
hunderttausende Dateien — und das fuer JEDEN laufenden Agenten, alle fuenf
Minuten.

Zwei Gegenmittel:

1. **Niedrigste Prioritaet.** Eine Hintergrundmessung darf echter Arbeit nie
   die Maschine wegnehmen.
2. **Nur messen, wo es noetig ist.** Ein Arbeitsbereich bei 2 % wird nicht
   binnen fuenf Minuten voll. Nah an der Grenze wird weiter jeden Durchlauf
   gemessen — dort zaehlt jede Minute.
"""

import unittest
from unittest.mock import MagicMock

from app.services.disk_monitor import (
    DiskMonitorService,
    _GENAU_HINSEHEN_AB,
    _RUHIG_UEBERSPRINGEN,
)

class _FakeContainer:
    """Schreibt mit, welcher Befehl wirklich im Container landet."""

    def __init__(self):
        self.befehle = []

    def exec_run(self, cmd, **kwargs):
        self.befehle.append(cmd)
        return 0, (b"1234\t/workspace\n", b"")


class _FakeClient:
    def __init__(self, container):
        self.containers = self
        self._c = container

    def get(self, _id):
        return self._c


class PrioritaetTest(unittest.TestCase):
    """Die Messung laeuft nachrangig — nachgemessen am echten Aufruf.

    Bewusst NICHT ueber ein Zeichenfenster im Quelltext (#726): Das misst
    einen Abstand, gemeint ist das Verhalten — und ein auskommentierter Aufruf
    bestuende die Pruefung klaglos. Hier wird der Befehl abgefangen, der
    tatsaechlich an den Container geht.
    """

    def _messen(self):
        from app.services.docker_service import DockerService

        container = _FakeContainer()
        dienst = DockerService.__new__(DockerService)
        dienst.client = _FakeClient(container)
        ergebnis = dienst.get_workspace_disk_usage("c1", 10.0)
        return ergebnis, container.befehle

    def test_die_messung_kommt_ueberhaupt_zurueck(self):
        """Sonst pruefen die folgenden Faelle einen Aufruf, der nie lief."""
        ergebnis, befehle = self._messen()
        self.assertIsNotNone(ergebnis)
        self.assertEqual(ergebnis["disk_usage_mb"], 1234.0)
        self.assertEqual(len(befehle), 1)

    def test_der_befehl_laeuft_mit_niedrigster_prioritaet(self):
        _, befehle = self._messen()
        text = " ".join(befehle[0]) if isinstance(befehle[0], list) else str(befehle[0])
        self.assertIn("nice -n 19", text,
                      "Ohne nice konkurriert die Messung mit echter Arbeit — "
                      "am 25.09.2026 mit 97 % CPU gemessen.")

    def test_es_gibt_einen_rueckfall_ohne_ionice(self):
        """``ionice`` fehlt in manchen Abbildern — dann darf nichts scheitern."""
        _, befehle = self._messen()
        text = " ".join(befehle[0]) if isinstance(befehle[0], list) else str(befehle[0])
        self.assertIn("||", text, "Kein Rueckfall, wenn ionice fehlt.")
        self.assertEqual(text.count("du -sm"), 2,
                         "Der Rueckfall muss dieselbe Messung ohne ionice fahren.")


class TaktTest(unittest.TestCase):
    """Gemessen wird, wo es noetig ist."""

    def _monitor(self):
        return DiskMonitorService(session_factory=MagicMock(),
                                  docker_service=MagicMock())

    def test_beim_ersten_mal_wird_immer_gemessen(self):
        m = self._monitor()
        self.assertTrue(m._muss_gemessen_werden("neu"))

    def test_ein_voller_arbeitsbereich_wird_jedes_mal_gemessen(self):
        """Nah an der Grenze zaehlt jede Minute."""
        m = self._monitor()
        m._naechsten_takt_festlegen("voll", _GENAU_HINSEHEN_AB + 5)
        for durchlauf in range(4):
            with self.subTest(durchlauf=durchlauf):
                self.assertTrue(m._muss_gemessen_werden("voll"))

    def test_ein_ruhiger_arbeitsbereich_wird_uebersprungen(self):
        m = self._monitor()
        m._naechsten_takt_festlegen("ruhig", 2.0)
        uebersprungen = 0
        while not m._muss_gemessen_werden("ruhig"):
            uebersprungen += 1
            if uebersprungen > 50:
                self.fail("Wird gar nicht mehr gemessen — das waere zu viel des Guten.")
        self.assertEqual(uebersprungen, _RUHIG_UEBERSPRINGEN)

    def test_er_wird_wieder_gemessen_sobald_er_voll_laeuft(self):
        """Sonst bliebe ein volllaufender Agent unbemerkt."""
        m = self._monitor()
        m._naechsten_takt_festlegen("waechst", 10.0)
        m._ueberspringen["waechst"] = 0          # naechster Durchlauf ist dran
        self.assertTrue(m._muss_gemessen_werden("waechst"))
        m._naechsten_takt_festlegen("waechst", 88.0)
        self.assertTrue(m._muss_gemessen_werden("waechst"),
                        "Ab jetzt muss wieder jeder Durchlauf messen.")

    def test_die_schwelle_liegt_unter_der_warnschwelle(self):
        """Sonst wuerde erst genau hingesehen, wenn schon gewarnt wird."""
        from app.services.disk_monitor import _WARN_THRESHOLD
        self.assertLess(_GENAU_HINSEHEN_AB, _WARN_THRESHOLD)


if __name__ == "__main__":
    unittest.main()
