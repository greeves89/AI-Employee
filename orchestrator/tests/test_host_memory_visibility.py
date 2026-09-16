"""Ein vom Kernel abgeschossener Lauf darf nicht spurlos verschwinden.

Befund #653: Auf einem Host mit ``cgroup_disable=memory`` fehlt der
Speicher-Controller. Docker kann dann kein Limit je Container durchsetzen
(``mem_limit`` wird still ignoriert), es gibt keine Buchfuehrung je Container,
und bei Knappheit beendet der Kernel den groessten Prozess — meist einen
laufenden Agenten.

Der Orchestrator sieht davon nur ``Connection closed by server``. Zehn von
neunzehn fehlgeschlagenen Aufgaben einer Woche gingen darauf zurueck, und die
Suche lief jedes Mal ins Leere: die Fehlerklasse wurde erst dem pids-Limit,
dann einem Sentinel-Stopp zugeschrieben — beides falsch.

Gemessen: ein Lauf belegt rund 1,04 GB, davon 691 MB die MCP-Server. Vier
gleichzeitige Laeufe sind 4,2 GB auf einem Host mit 7,95 GB.

Dieses Modul stellt nur Sichtbarkeit her. Der grosse Hebel ist #638 (MCP-Server
container-weit statt je Lauf), die Host-Aenderung selbst entscheidet der
Betreiber.
"""

import ast
import unittest
import unittest.mock
from pathlib import Path

from app.core import host_memory
from app.core.run_outcome import erklaerung_fuer_abriss

_ROUTER = (Path(__file__).resolve().parents[1] / "app" / "core" / "task_router.py").read_text()
_MAIN = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()


def _ohne_kommentare(block: str) -> str:
    """Kommentare aus einem Quelltextblock tilgen (per tokenize, nicht per
    '#'-Suche). Ein auskommentierter Aufruf stuende sonst weiterhin im Block
    und bestuende jedes `assertIn` — die Blindstelle aus #726."""
    import io
    import textwrap
    import tokenize

    text = textwrap.dedent(block)
    zeilen = text.splitlines(keepends=True)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                (zeile, von), (_, bis) = tok.start, tok.end
                zeilen[zeile - 1] = zeilen[zeile - 1][:von] + zeilen[zeile - 1][bis:]
    except tokenize.TokenError as e:  # unvollstaendiger Block — lieber laut
        raise AssertionError(f"Block nicht tokenisierbar: {e}")
    return "".join(zeilen)


def _knotenquelle(src: str, knoten: ast.AST) -> str:
    """``ast.get_source_segment`` mit korrigierter erster Zeile.

    Die Funktion liefert die erste Zeile OHNE ihre urspruengliche Einrueckung
    (sie schneidet ab ``col_offset``), alle folgenden Zeilen aber MIT ihrer
    vollen Original-Einrueckung. Bei einem eingerueckten Knoten mit
    Geschwister-Klausel auf gleicher Spalte (``except`` zu ``try``, ``elif``/
    ``else`` zu ``if``) ergibt das eine Zeile, die scheinbar auf Spalte 0
    zurueckspringt, ohne dass diese Ebene je geoeffnet wurde — ``textwrap.dedent``
    findet dann keinen gemeinsamen Praefix mehr. Die fehlende Einrueckung der
    ersten Zeile hier wieder auffuellen, bevor gekuerzt wird."""
    text = ast.get_source_segment(src, knoten) or ""
    return (" " * knoten.col_offset) + text


def _try_block(src: str, ruf: str) -> str:
    """Der kleinste try/except-Block, der ``ruf`` aufruft — als AST-Knoten,
    nicht als geschaetzte Zeichenzahl. Ein laengerer Kommentar daneben
    verschiebt die Grenze nicht, und Kommentare werden vor der Pruefung
    getilgt."""
    treffer = []
    for knoten in ast.walk(ast.parse(src)):
        if isinstance(knoten, ast.Try):
            text = _knotenquelle(src, knoten)
            if ruf in text:
                treffer.append(text)
    if not treffer:
        raise AssertionError(f"try/except um {ruf!r} nicht gefunden")
    return _ohne_kommentare(min(treffer, key=len))


class DieHostPruefungTests(unittest.TestCase):
    def test_ohne_lesbares_dateisystem_wird_nichts_behauptet(self):
        """Auf einem Rechner ohne cgroup2 (macOS, BSD) gibt es nichts zu melden —
        eine Warnung dort waere Rauschen."""
        with unittest.mock.patch.object(host_memory, "_CONTROLLER",
                                        Path("/nicht/vorhanden")):
            self.assertIsNone(host_memory.speicher_controller_da())
            self.assertIsNone(host_memory.hinweis())

    def test_vorhandener_controller_meldet_nichts(self):
        with unittest.mock.patch.object(host_memory, "speicher_controller_da",
                                        return_value=True):
            self.assertIsNone(host_memory.hinweis())

    def test_fehlender_controller_wird_gemeldet(self):
        with unittest.mock.patch.object(host_memory, "speicher_controller_da",
                                        return_value=False), \
             unittest.mock.patch.object(host_memory, "abgeschaltet_per_kernelzeile",
                                        return_value=False):
            text = host_memory.hinweis()
        self.assertIsNotNone(text)
        self.assertIn("wirkungslos", text)
        self.assertIn("Connection closed by server", text)

    def test_der_behebbare_fall_nennt_die_ursache(self):
        """`cgroup_disable=memory` ist entfernbar — das gehoert in die Meldung,
        sonst weiss niemand, dass sich daran etwas aendern laesst."""
        with unittest.mock.patch.object(host_memory, "speicher_controller_da",
                                        return_value=False), \
             unittest.mock.patch.object(host_memory, "abgeschaltet_per_kernelzeile",
                                        return_value=True):
            text = host_memory.hinweis()
        self.assertIn("cgroup_disable=memory", text)
        self.assertIn("Neustart", text)

    def test_die_pruefung_laeuft_beim_start(self):
        self.assertIn("beim_start_melden()", _MAIN)

    def test_ein_fehler_dabei_haelt_den_start_nicht_auf(self):
        """``_try_block`` statt eines 300-Zeichen-Fensters: ein auskommentiertes
        ``except Exception`` bestuende das Fenster klaglos, hier nicht — dann
        risse ein Fehler bei der Pruefung den ganzen Start ab."""
        block = _try_block(_MAIN, "beim_start_melden()")
        self.assertIn("except Exception", block)


class DerAbrissBekommtSeineErklaerungTests(unittest.IsolatedAsyncioTestCase):
    def test_nur_bei_fehlendem_controller(self):
        """Auf einem gesunden Host ist der Abriss etwas anderes — dort waere die
        Erklaerung eine Irrefuehrung."""
        with unittest.mock.patch.object(host_memory, "speicher_controller_da",
                                        return_value=True):
            self.assertIsNone(erklaerung_fuer_abriss("Connection closed by server"))

    def test_bei_fehlendem_controller_wird_erklaert(self):
        with unittest.mock.patch.object(host_memory, "speicher_controller_da",
                                        return_value=False):
            text = erklaerung_fuer_abriss("Consumer error: Connection closed by server.")
        self.assertIsNotNone(text)
        self.assertIn("Kernel", text)
        self.assertIn("#653", text)

    def test_andere_fehler_bekommen_keine_erklaerung(self):
        """Sonst stuende bei jedem Fehlschlag derselbe Absatz — und niemand
        laese ihn mehr."""
        with unittest.mock.patch.object(host_memory, "speicher_controller_da",
                                        return_value=False):
            for anderer in ("401 Unauthorized", "FileNotFoundError: /tmp/x",
                            "You've hit your limit", ""):
                self.assertIsNone(erklaerung_fuer_abriss(anderer), anderer)

    def test_sie_haengt_am_ergebnispfad(self):
        self.assertIn("erklaerung_fuer_abriss(task.error)", _ROUTER)

    async def test_der_urspruengliche_text_bleibt_erhalten(self):
        """Die Erklaerung tritt hinzu, sie ersetzt nicht. Statt eines
        300-Zeichen-Fensters wird ``TaskRouter.handle_task_completion`` wirklich
        gefahren: der Originaltext muss im Endergebnis noch als Praefix stecken
        — nicht nur als Satzteil irgendwo in der Naehe des Aufrufs."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.core.task_router import TaskRouter

        task = MagicMock(id="t1", agent_id=None, retain=False, metadata_={})
        ergebnis = MagicMock()
        ergebnis.scalar_one_or_none = MagicMock(return_value=task)
        db = MagicMock()
        db.execute = AsyncMock(return_value=ergebnis)
        db.commit = AsyncMock()

        router = TaskRouter.__new__(TaskRouter)
        router.db = db

        with unittest.mock.patch.object(host_memory, "speicher_controller_da",
                                        return_value=False):
            await router.handle_task_completion({
                "task_id": "t1", "status": "failed",
                "error": "Connection closed by server",
            })

        self.assertTrue(
            task.error.startswith("Connection closed by server — "),
            f"Originaltext nicht erhalten geblieben: {task.error!r}")


if __name__ == "__main__":
    unittest.main()
