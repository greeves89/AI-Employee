"""Regression tests: a failed LLM self-reflection must name the exception TYPE (#857).

Warum diese Tests existieren
----------------------------
Die alte Fassung protokollierte ausschliesslich ``{exc}``::

    logger.warning(f"... falling back to formula: {exc}")

Gemessen in ``/shared/platform-errors.log`` (07.09.-25.09.2026): 161 Vorkommen, 161
verschiedene Aufgaben-IDs — und **156 davon (96,9 %) mit leerem Fehlertext**, weil
mehrere hier erreichbare Ausnahmeklassen ohne Argumente erzeugt werden und dann eine
LEERE Stringform haben (``str(TimeoutError()) == ""``). Die Zeile endete auf
``formula: `` und dann nichts: der Ausfall war grundsaetzlich nicht zuordenbar.

Die Zusicherung lautet deshalb NICHT "es wird gewarnt", sondern "die Warnung nennt den
TYP". Genau diese Unterscheidung fehlte — ein Test auf die Existenz der Warnung waere
gegen den alten Code gruen geblieben und haette die Blindstelle nicht gefunden.
"""

import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.core.task_router import (
    _REFLECTION_REAP_TIMEOUT_S,  # noqa: F401  (in Quelltexttest geprueft)
    _REFLECTION_TIMEOUT_S,
    _compute_formula_rating,
    _llm_reflect_on_task,
)
from app.models.task import TaskStatus

_LOGGER = "app.core.task_router"


def _task(task_id="t857"):
    return SimpleNamespace(
        id=task_id,
        title="Beispielaufgabe",
        prompt="Mach etwas.",
        result="Fertig.",
        error=None,
        status=TaskStatus.COMPLETED,
        duration_ms=1000,
        num_turns=3,
        cost_usd=0.01,
    )


class _FakeProc:
    returncode = 0

    def __init__(self):
        self.killed = False
        self.waited = False

    async def communicate(self):  # pragma: no cover - never awaited in these tests
        return b"", b""

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True
        return 0


_SPAWNED: list = []


async def _fake_exec(*_args, **_kwargs):
    proc = _FakeProc()
    _SPAWNED.append(proc)
    return proc


class ReflectionFailureLoggingTests(unittest.IsolatedAsyncioTestCase):
    """Jeder Fehlerpfad muss den Ausnahmetyp im Protokoll hinterlassen."""

    def setUp(self):
        # Ohne die claude-CLI kehrt die Funktion vor dem try-Block zurueck und kein
        # Fehlerpfad wird je betreten — der Test waere dann leer gruen.
        self._which = patch("shutil.which", return_value="/usr/bin/claude")
        self._exec = patch("asyncio.create_subprocess_exec", new=_fake_exec)
        # Seit 1.346.4 braucht der Claude-Weg auch einen Zugang — sonst Formel.
        self._zugang = patch("app.config.settings.claude_code_oauth_token", "test-token")
        self._which.start()
        self._exec.start()
        self._zugang.start()
        self.addCleanup(self._which.stop)
        self.addCleanup(self._exec.stop)
        self.addCleanup(self._zugang.stop)
        _SPAWNED.clear()
        self.observed_timeout = None

    async def _run_with_wait_for(self, boom):
        test = self

        real_wait_for = asyncio.wait_for
        calls = []

        async def fake_wait_for(awaitable, timeout=None):
            # NUR der erste Aufruf ist der ueberwachte `communicate()`. Der Timeout-Zweig
            # ruft wait_for ein ZWEITES Mal fuer das begrenzte Aufraeumen — wuerde der
            # Doppel auch dort werfen, traege der Test einen Fehler in den Fehlerpfad und
            # das aufgezeichnete Budget waere das des Aufraeumens statt des Aufrufs.
            calls.append(timeout)
            if len(calls) > 1:
                return await real_wait_for(awaitable, timeout=timeout)
            # Das TATSAECHLICH erzwungene Budget mitschreiben — nur so kann der Test
            # bemerken, wenn Protokolltext und Wirklichkeit auseinanderlaufen.
            test.observed_timeout = timeout
            awaitable.close()  # sonst RuntimeWarning: coroutine never awaited
            raise boom

        with patch("asyncio.wait_for", new=fake_wait_for):
            with self.assertLogs(_LOGGER, level=logging.WARNING) as captured:
                result = await _llm_reflect_on_task(_task())
        return result, "\n".join(captured.output)

    def _assert_is_formula_fallback(self, result):
        """Der Fallback darf weder ein Erfuellungs-Urteil erfinden noch die Formel umgehen."""
        self.assertEqual(result[1], "auto-rated (formula fallback)")
        self.assertIsNone(result[2], "Formel-Fallback darf kein fulfilled-Urteil erfinden")
        self.assertEqual(result[3], "")
        self.assertEqual(
            result[0],
            _compute_formula_rating(_task()),
            "Bewertung muss aus der Formel kommen, nicht aus einer festen Zahl",
        )

    async def test_timeout_names_the_type_and_the_enforced_budget(self):
        result, log = await self._run_with_wait_for(asyncio.TimeoutError())

        # Der Typ ist der eigentliche Befund — genau er fehlte in 156 Faellen.
        self.assertIn("TimeoutError", log)
        # Das Budget gehoert dazu, sonst ist "es lief zu lange" nicht nachrechenbar.
        # Verglichen wird gegen das WIRKLICH uebergebene Budget, nicht gegen die
        # Konstante: sonst bliebe eine Abweichung wie `_REFLECTION_TIMEOUT_S * 3`
        # unbemerkt und die Protokollzeile wuerde eine falsche Zahl nennen.
        self.assertIsNotNone(self.observed_timeout, "wait_for wurde ohne Budget gerufen")
        self.assertEqual(self.observed_timeout, _REFLECTION_TIMEOUT_S)
        self.assertIn(f"timeout={self.observed_timeout}s", log)
        # Und die Zeile darf nicht mehr nach "formula: " abbrechen.
        self.assertFalse(
            log.rstrip().endswith("falling back to formula:"),
            "Protokollzeile endet wieder ohne Fehlerangabe",
        )
        self._assert_is_formula_fallback(result)

    async def test_timeout_reaps_the_child_instead_of_leaking_it(self):
        """``wait_for`` bricht nur die Koroutine ab — das Kind lebt weiter.

        Ohne das Einsammeln bleibt je Zeitablauf ein ``claude``-Prozess samt seiner
        Dateikennungen zurueck; bei der gemessenen Haeufigkeit (~8 Zeitablaeufe/Tag)
        summiert sich das ueber die Laufzeit des Behaelters.
        """
        await self._run_with_wait_for(asyncio.TimeoutError())

        self.assertEqual(len(_SPAWNED), 1, "Vorbedingung: genau ein Kind erzeugt")
        self.assertTrue(_SPAWNED[0].killed, "Kindprozess wurde nicht beendet")
        self.assertTrue(_SPAWNED[0].waited, "Kindprozess wurde nicht eingesammelt (Zombie)")

    async def test_timeout_keeps_a_real_errno_message_instead_of_overwriting_it(self):
        """``TimeoutError`` ist eine ``OSError``-Unterklasse — der Zweig faengt mehr als die Frist.

        Ein ``OSError`` mit ``errno`` ETIMEDOUT aus der Unterprozess-Maschinerie ist eine
        ``TimeoutError``-Instanz und traegt eine echte Meldung. Ein fest verdrahteter
        Budget-Satz wuerde sie ueberschreiben und damit genau den Informationsverlust
        wiederherstellen, den diese Aenderung beseitigt.
        """
        import errno

        boom = OSError(errno.ETIMEDOUT, "connection timed out")
        self.assertIsInstance(boom, asyncio.TimeoutError, "Vorbedingung des Tests")
        self.assertTrue(str(boom), "Vorbedingung: diese Instanz HAT eine Meldung")

        result, log = await self._run_with_wait_for(boom)

        self.assertIn("connection timed out", log)
        self.assertNotIn(
            "wait_for budget exceeded",
            log,
            "Budget-Satz behauptet eine Fristueberschreitung, die nicht stattfand",
        )
        self._assert_is_formula_fallback(result)

    async def test_timeout_while_spawning_does_not_crash_the_handler(self):
        """Der Zeitablauf-Zweig muss auch ohne Kindprozess tragen.

        ``TimeoutError`` ist eine ``OSError``-Unterklasse, kann also schon aus
        ``create_subprocess_exec`` selbst kommen — dann existiert kein ``proc``. Ohne die
        Vorbelegung waere der Fehlerpfad ein ``NameError``, der aus der Bewertung
        herausfliegt und die Aufgabenfertigstellung mitreisst: aus einem behandelten
        Fehler wuerde ein unbehandelter.
        """
        import errno

        async def exploding_exec(*_args, **_kwargs):
            raise OSError(errno.ETIMEDOUT, "connection timed out")

        with patch("asyncio.create_subprocess_exec", new=exploding_exec):
            with self.assertLogs(_LOGGER, level=logging.WARNING) as captured:
                result = await _llm_reflect_on_task(_task())

        log = "\n".join(captured.output)
        self.assertIn("TimeoutError", log)
        self.assertIn("connection timed out", log)
        self.assertEqual(_SPAWNED, [], "Vorbedingung: es wurde kein Kind erzeugt")
        self._assert_is_formula_fallback(result)

    async def test_exception_without_message_still_names_its_type(self):
        """Die Klasse des Fehlers, nicht nur der Zeitablauf.

        Jede ohne Argumente erzeugte Ausnahme hat eine leere Stringform. Stellvertretend
        ``RuntimeError()`` — ``{exc}`` allein ergaebe hier wieder eine leere Meldung.
        """
        self.assertEqual(str(RuntimeError()), "", "Vorbedingung: Stringform ist leer")

        result, log = await self._run_with_wait_for(RuntimeError())

        self.assertIn("RuntimeError", log)
        self.assertFalse(log.rstrip().endswith("falling back to formula:"))
        self._assert_is_formula_fallback(result)

    async def test_exception_with_message_keeps_its_message(self):
        """Die 5 auswertbaren Faelle (401/session limit) duerfen nicht verlieren."""
        result, log = await self._run_with_wait_for(
            ValueError("claude reflection unusable (is_error=True): 401 revoked")
        )

        self.assertIn("ValueError", log)
        self.assertIn("401 revoked", log)
        self._assert_is_formula_fallback(result)

    async def test_cancellation_propagates_and_is_not_rated(self):
        """Ein Abbruch der Aufgabe ist keine gescheiterte Bewertung.

        ``CancelledError`` erbt seit Python 3.8 direkt von ``BaseException`` und wird von
        ``except Exception`` ohnehin nicht gefangen; der Test haelt dieses Verhalten fest,
        damit ein spaeterer Umbau (etwa ein breiteres ``except BaseException``) den
        Abbruch nicht still in eine Formelbewertung verwandelt.
        """
        self.assertFalse(
            issubclass(asyncio.CancelledError, Exception),
            "Vorbedingung: CancelledError ist keine Exception-Subklasse",
        )

        async def fake_wait_for(awaitable, timeout=None):
            awaitable.close()
            raise asyncio.CancelledError()

        with patch("asyncio.wait_for", new=fake_wait_for):
            with self.assertRaises(asyncio.CancelledError):
                await _llm_reflect_on_task(_task())


class RealChildReapBoundTests(unittest.IsolatedAsyncioTestCase):
    """Das Aufraeumen nach dem Zeitablauf darf die Bewertung nicht unbegrenzt aufhalten.

    Warum das ein EIGENER Test mit ECHTEN Prozessen sein muss
    --------------------------------------------------------
    ``_FakeProc.wait()`` oben setzt ein Boolean und kehrt sofort zurueck. Damit ist
    beweisbar, DASS eingesammelt wird — aber grundsaetzlich nicht, ob das Einsammeln
    jemals endet. Genau dort sass der Fehler: ``await proc.wait()`` ohne Frist ist
    unbegrenzt, weil der Prozess-Waiter unter dem normalen asyncio-Loop zusaetzlich auf
    das Schliessen der Pipe-TRANSPORTE wartet. Ein Enkelprozess, der stdout/stderr des
    Kindes geerbt hat, haelt diese offen — das direkte Kind ist laengst mit ``-9``
    eingesammelt, und die Bewertung kehrt trotzdem nie zurueck. Mit ihr haengen
    ``_auto_rate_task`` und die nachgelagerten Abschluss-Callbacks.

    Der Test startet deshalb ein echtes Kind, das einen echten Enkel mit geerbten
    Leitungen hinterlaesst. Der Aufruf selbst ist in ein ``wait_for`` gewickelt: ohne
    das wuerde der alte Code den Testlauf HAENGEN lassen statt rot zu werden, und ein
    haengender Test meldet keinen Fehler, er frisst nur den Lauf.
    """

    async def test_cleanup_is_bounded_when_a_descendant_holds_the_pipes_open(self):
        import os
        import signal
        import sys
        import tempfile
        import time

        pid_file = tempfile.NamedTemporaryFile(delete=False)
        pid_file.close()
        # Kind: startet einen Enkel, der stdout/stderr ERBT, und schlaeft dann selbst.
        child_code = (
            "import subprocess, sys, time\n"
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "open(sys.argv[1], 'w').write(str(g.pid))\n"
            "time.sleep(60)\n"
        )

        spawned = []
        # Die ECHTE Funktion festhalten, bevor der Patch sie ersetzt — sonst ruft der
        # Ersatz sich selbst auf, und die RecursionError landet im generischen
        # Fehlerzweig der Produktion: der Test saehe eine Warnung und haette nie einen
        # Prozess gestartet.
        real_exec = asyncio.create_subprocess_exec

        # Der Zeitpunkt, an dem die Vorbedingung steht und die gemessene Frist
        # beginnt. Ohne ihn wuerde die Wartezeit auf den Enkel in die Dauer-
        # Zusicherung unten einlaufen und sie unter Last zu Unrecht reissen.
        armed_at = []

        async def _exec_real(*_args, **_kwargs):
            proc = await real_exec(
                sys.executable, "-c", child_code, pid_file.name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            spawned.append(proc)
            # VORBEDINGUNG, nicht Bequemlichkeit: dieser Test prueft den Fall "ein
            # NACHKOMME haelt die Leitungen offen". Existiert der Enkel noch nicht,
            # wenn das Budget ablaeuft, schliesst `kill()` die Leitungen sofort, das
            # Aufraeumen glueckt, und `assertIn("could not confirm")` unten faellt
            # durch — der Test ist dann falsch ROT, obwohl die Produktion korrekt
            # ist. Gemessen auf 4 Kernen unter Last: 2 von 10 Laeufen rot.
            #
            # Hier zu warten ist der einzige Ort, der das deterministisch macht: die
            # Frist der Produktion laeuft erst ab der RUECKKEHR dieser Funktion, also
            # steht der Enkel garantiert, bevor die Uhr startet. Ein groesseres Budget
            # macht das Zeitfenster nur breiter, nicht zuverlaessig.
            deadline = time.monotonic() + 30.0
            while True:
                try:
                    if int(open(pid_file.name).read() or 0) > 0:
                        break
                except (OSError, ValueError):
                    pass
                if time.monotonic() > deadline:
                    self.fail(
                        "Vorbedingung nicht herstellbar: das Kind hat binnen 30 s "
                        "keinen Enkelprozess gestartet — der Test kann den Fall "
                        "'Nachkomme haelt die Leitungen offen' nicht messen."
                    )
                await asyncio.sleep(0.01)
            armed_at.append(time.monotonic())
            return proc

        # Knapp bleiben ist hier richtig: die Vorbedingung oben ist bereits
        # hergestellt, wenn die Uhr startet, also entscheidet das Budget nur noch,
        # wie lange der Test wartet — nicht mehr, OB er das Richtige misst.
        budget, reap = 0.2, 0.3
        patches = [
            # Ohne das kehrt die Funktion auf jeder Maschine ohne `claude`-CLI (CI!)
            # sofort mit "claude CLI not found" zurueck — kein Prozess, keine
            # Warnung, und der Test pruefte nichts, sondern schlug nur fehl.
            patch("shutil.which", return_value="/usr/local/bin/claude"),  # task_router importiert shutil lokal
            patch("app.config.settings.claude_code_oauth_token", "test-token"),
            patch("asyncio.create_subprocess_exec", new=_exec_real),
            patch("app.core.task_router._REFLECTION_TIMEOUT_S", budget),
            patch("app.core.task_router._REFLECTION_REAP_TIMEOUT_S", reap),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        # Das Warten auf den Enkel steckt in `_exec_real` (siehe dort): es MUSS
        # zwischen dem Start des Kindes und dem Ablauf des Budgets liegen, und nur
        # die gepatchte Startfunktion sitzt an dieser Stelle.
        try:
            with self.assertLogs(_LOGGER, level=logging.WARNING) as captured:
                result = await asyncio.wait_for(
                    _llm_reflect_on_task(_task("t857-real")),
                    # Grosszuegig ueber beiden Budgets: der ALTE, unbegrenzte Code
                    # laeuft hier in einen Fehlschlag statt den Lauf zu blockieren.
                    timeout=budget + reap + 5.0,
                )
        except asyncio.TimeoutError:  # pragma: no cover - nur beim Regress
            self.fail(
                "Die Bewertung kehrte nicht zurueck: das Aufraeumen nach dem "
                "Zeitablauf ist unbegrenzt, solange ein Nachkomme die Leitungen "
                "des Kindes offen haelt."
            )
        finally:
            for proc in spawned:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            try:
                grandchild = int(open(pid_file.name).read() or 0)
            except (OSError, ValueError):
                grandchild = 0
            if grandchild:
                try:
                    os.kill(grandchild, signal.SIGKILL)
                    os.waitpid(grandchild, 0)
                except (ProcessLookupError, ChildProcessError, PermissionError):
                    pass
            os.unlink(pid_file.name)

        # Das misslungene Aufraeumen muss SICHTBAR sein. Ohne diese Zusicherung bleibt
        # ein stiller `except asyncio.TimeoutError: pass` gruen — der Betreiber saehe
        # dann weder den Haenger noch die weiterhin gehaltenen Dateikennungen, und der
        # Befund aus #857 ("der Ausfall steht nicht im Protokoll") waere zurueck.
        log = "\n".join(captured.output)
        self.assertIn("could not confirm", log)
        self.assertIn(f"{reap}s", log)

        # Ab dem Moment gemessen, in dem die Vorbedingung stand und die Frist der
        # Produktion zu laufen begann — nicht ab Testbeginn. Das Herstellen der
        # Vorbedingung ist Aufbau und darf die Frist-Zusicherung nicht mitbelasten.
        self.assertEqual(len(armed_at), 1, "Vorbedingung: genau ein Startzeitpunkt")
        elapsed = time.monotonic() - armed_at[0]
        self.assertLess(
            elapsed, budget + reap + 4.0,
            f"Aufruf brauchte {elapsed:.2f}s - das Aufraeumen ist nicht begrenzt",
        )
        # Vorbedingung: der Zeitablauf-Zweig wurde wirklich betreten.
        self.assertEqual(len(spawned), 1, "Vorbedingung: genau ein echtes Kind erzeugt")
        self.assertIsNotNone(spawned[0].returncode, "Kind wurde nicht eingesammelt")
        # Und das Ergebnis ist trotz unvollstaendigem Aufraeumen der Formel-Fallback,
        # nicht etwa gar keines.
        self.assertEqual(result[0], _compute_formula_rating(_task("t857-real")))
        self.assertEqual(result[1], "auto-rated (formula fallback)")


class ReflectionTimeoutConstantTests(unittest.TestCase):
    def test_call_site_uses_the_constant_not_a_literal(self):
        """Protokolltext und erzwungenes Budget duerfen nicht auseinanderlaufen.

        Der Timeout-Zweig nennt ``_REFLECTION_TIMEOUT_S`` im Text. Stuende am
        ``wait_for``-Aufruf weiterhin eine Zahl im Quelltext, wuerde eine spaetere
        Aenderung dieser Zahl die Protokollzeile zu einer Falschaussage machen, ohne
        dass ein Test rot wird.
        """
        import inspect

        from app.core import task_router

        src = inspect.getsource(task_router._llm_reflect_on_task)
        self.assertIn("timeout=_REFLECTION_TIMEOUT_S", src)
        self.assertNotIn("timeout=20.0", src)
        # Dasselbe gilt fuer die Frist des Aufraeumens: ein blankes `await proc.wait()`
        # ohne wait_for ist der Merge-Blocker, den dieser PR behebt.
        self.assertIn("timeout=_REFLECTION_REAP_TIMEOUT_S", src)
        self.assertNotIn("await proc.wait()\n", src)
        # Die eigentliche Zusicherung liegt im Verhaltenstest
        # test_timeout_names_the_type_and_the_enforced_budget, der das WIRKLICH
        # uebergebene Budget mit der Zahl im Protokoll vergleicht. Dieser Quelltext-
        # Test haelt nur die Absicht fest, am Aufrufort keine Zahl zu hinterlassen.


if __name__ == "__main__":
    unittest.main()


class OhneClaudeZugangTests(unittest.IsolatedAsyncioTestCase):
    """29.09.2026, Kundenanlage nur mit Azure: CLI installiert, aber kein Zugang —
    jede Aufgabe scheiterte an der Selbstbewertung und landete als Warnung in der
    Fehlerdatei. Ohne Zugang gleich die Formel, ohne Aufruf."""

    async def test_kein_zugang_heisst_formel_ohne_aufruf(self):
        from types import SimpleNamespace
        from app.core import task_router

        aufrufe = []

        async def zaehlt(*a, **k):
            aufrufe.append(a)
            raise AssertionError("claude darf nicht aufgerufen werden")

        aufgabe = SimpleNamespace(id="t1", title="t", status=SimpleNamespace(value="completed"),
                                  duration_ms=1000, num_turns=1, cost_usd=0, error=None,
                                  prompt="p", result="r")
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("asyncio.create_subprocess_exec", new=zaehlt), \
             patch("app.config.settings.claude_code_oauth_token", ""), \
             patch("app.config.settings.anthropic_api_key", ""), \
             patch.object(task_router, "_compute_formula_rating", return_value=4):
            wertung, grund, _, _ = await task_router._llm_reflect_on_task(aufgabe)
        self.assertEqual((wertung, aufrufe), (4, []))
        self.assertIn("kein Claude-Zugang", grund)

