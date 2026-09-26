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
        self._which.start()
        self._exec.start()
        self.addCleanup(self._which.stop)
        self.addCleanup(self._exec.stop)
        _SPAWNED.clear()
        self.observed_timeout = None

    async def _run_with_wait_for(self, boom):
        test = self

        async def fake_wait_for(awaitable, timeout=None):
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
        # Die eigentliche Zusicherung liegt im Verhaltenstest
        # test_timeout_names_the_type_and_the_enforced_budget, der das WIRKLICH
        # uebergebene Budget mit der Zahl im Protokoll vergleicht. Dieser Quelltext-
        # Test haelt nur die Absicht fest, am Aufrufort keine Zahl zu hinterlassen.


if __name__ == "__main__":
    unittest.main()
