"""Die Wiederholung nach Token-Rotation muss auch greifen, wenn der CLI den 401
als ``result``-Ereignis meldet (#799).

Der Anlass, drei Tage in Folge auf die Minute gleich:

    16:20  Proaktivlauf startet
    16:46  „Failed to authenticate. API Error: 401 OAuth access token has been revoked."
    16:47  Lauf steht auf failed, „(Versuch 2)" startet von vorn — 26 min Arbeit weg

``execute_task`` HAT eine Wiederholung — sie prueft aber ``status == "error"``,
und der Claude-CLI meldet einen 401 mitten im Lauf nicht als Fehler-Exit, sondern
als ``result`` mit ``is_error`` und dem 401-Text. ``_execute_task_once`` mappte
jedes ``result`` auf „completed" und warf ``is_error`` weg — die Wiederholung
griff nie. Der Quelltext-Test in test_auth_rotation_retry (``assertIn``) konnte
das nicht sehen; deshalb hier Verhalten mit Doubles.
"""

import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, patch

REVOKED = "Failed to authenticate. API Error: 401 OAuth access token has been revoked."


class ZugangVerlorenTests(unittest.TestCase):
    """Die Entscheidung selbst, ohne Runner."""

    def test_fehler_exit_mit_401(self):
        from app.ai_credential_status import zugang_verloren
        self.assertTrue(zugang_verloren({"status": "error", "error": "401 Unauthorized"}))

    def test_result_mit_is_error_und_401_text(self):
        """Genau die Form aus dem Betrieb."""
        from app.ai_credential_status import zugang_verloren
        self.assertTrue(zugang_verloren(
            {"status": "completed", "is_error": True,
             "result": REVOKED, "is_error_text": REVOKED}))

    def test_bei_is_error_zaehlt_nur_der_fehlertext_nicht_der_agententext(self):
        """``result`` kann bei leerem CLI-Fehlertext den gesammelten Agententext
        tragen — wer an einem OAuth-Thema arbeitet, hat 401 und oauth im Text."""
        from app.ai_credential_status import zugang_verloren
        self.assertFalse(zugang_verloren({
            "status": "completed", "is_error": True,
            "result": "Ich baue gerade den Fix fuer 401 OAuth revoked ...",
            "is_error_text": "Something went wrong",
        }))
        self.assertTrue(zugang_verloren({
            "status": "completed", "is_error": True,
            "result": "Ich pruefe die Migration ...",
            "is_error_text": REVOKED,
        }))

    def test_erfolgsbericht_der_ueber_einen_401_berichtet_zaehlt_nicht(self):
        """Ein Agent, der ueber Anmeldefehler BERICHTET, hat gearbeitet."""
        from app.ai_credential_status import zugang_verloren
        self.assertFalse(zugang_verloren({
            "status": "completed", "is_error": False,
            "result": "Bericht: der Lauf gestern starb an 401 OAuth revoked — Issue angelegt.",
        }))
        self.assertFalse(zugang_verloren({
            "status": "completed",
            "result": "Bericht: der Lauf gestern starb an 401 OAuth revoked.",
        }))

    def test_is_error_ohne_zugangsbezug_zaehlt_nicht(self):
        """Kontextlimit ist kein Zugangsproblem — Warten auf einen Token hilft nicht."""
        from app.ai_credential_status import zugang_verloren
        self.assertFalse(zugang_verloren(
            {"status": "completed", "is_error": True,
             "result": "Prompt is too long", "is_error_text": "Prompt is too long"}))

    def test_fehler_exit_ohne_zugangsbezug_zaehlt_nicht(self):
        from app.ai_credential_status import zugang_verloren
        self.assertFalse(zugang_verloren(
            {"status": "error", "error": "Claude CLI exited with code 1: boom"}))


class RunnerWiederholtTests(unittest.IsolatedAsyncioTestCase):
    """``execute_task`` mit Double fuer den einzelnen Lauf."""

    def _runner(self, *ergebnisse):
        from app.agent_runner import AgentRunner
        runner = AgentRunner(log_publisher=AsyncMock())
        runner._execute_task_once = AsyncMock(side_effect=list(ergebnisse))
        return runner

    async def test_is_error_401_wird_nach_neuem_token_wiederholt(self):
        runner = self._runner(
            {"status": "completed", "is_error": True, "result": REVOKED,
             "is_error_text": REVOKED, "num_turns": 40},
            {"status": "completed", "is_error": False, "result": "fertig", "num_turns": 3},
        )
        warten = AsyncMock(return_value="neu")
        with patch("app.config.get_oauth_token", lambda: "alt"), \
             patch("app.config.wait_for_new_oauth_token", warten), \
             patch("app.agent_runner.report_result_status", AsyncMock()):
            result = await runner.execute_task("t1", "arbeite")

        self.assertEqual(runner._execute_task_once.await_count, 2)
        warten.assert_awaited_once_with("alt")
        self.assertEqual(result["result"], "fertig")

    async def test_ein_bericht_ueber_einen_401_wird_nicht_wiederholt(self):
        """Sonst liefe jeder Lauf zweimal, der das Wort 401 im Bericht hat."""
        runner = self._runner({
            "status": "completed", "is_error": False,
            "result": "Issue angelegt: Laeufe sterben an 401 OAuth revoked.",
        })
        warten = AsyncMock()
        with patch("app.config.wait_for_new_oauth_token", warten), \
             patch("app.agent_runner.report_result_status", AsyncMock()):
            await runner.execute_task("t1", "arbeite")
        self.assertEqual(runner._execute_task_once.await_count, 1)
        warten.assert_not_awaited()

    async def test_kontextlimit_wird_nicht_wiederholt(self):
        runner = self._runner(
            {"status": "completed", "is_error": True,
             "result": "Prompt is too long", "is_error_text": "Prompt is too long"})
        warten = AsyncMock()
        with patch("app.config.wait_for_new_oauth_token", warten), \
             patch("app.agent_runner.report_result_status", AsyncMock()):
            result = await runner.execute_task("t1", "arbeite")
        self.assertEqual(runner._execute_task_once.await_count, 1)
        warten.assert_not_awaited()
        # Vertrag nach aussen unveraendert: der Orchestrator erkennt den Text selbst.
        self.assertEqual(result, {"status": "completed", "result": "Prompt is too long"})

    async def test_nach_aussen_bleibt_der_vertrag_gleich(self):
        """Kein ``is_error`` im Ergebnis fuer den Orchestrator — auch nicht, wenn
        die Wiederholung ebenfalls scheitert. Der Orchestrator erkennt den
        401-Text im result (Serien-Alarm #680) und stuft die rohe Meldung als
        dauerhaft ein; ein neues Feld oder ein anderer Status aendert das."""
        runner = self._runner(
            {"status": "completed", "is_error": True, "result": REVOKED, "is_error_text": REVOKED},
            {"status": "completed", "is_error": True, "result": REVOKED, "is_error_text": REVOKED},
        )
        with patch("app.config.get_oauth_token", lambda: "alt"), \
             patch("app.config.wait_for_new_oauth_token", AsyncMock(return_value=None)), \
             patch("app.agent_runner.report_result_status", AsyncMock()):
            result = await runner.execute_task("t1", "arbeite")
        self.assertEqual(runner._execute_task_once.await_count, 2)
        self.assertEqual(result, {"status": "completed", "result": REVOKED})

    async def test_zugangsstatus_wird_als_auth_failed_gemeldet(self):
        """Vorher meldete der Runner „ok" fuer einen Lauf, der am widerrufenen
        Token gestorben war."""
        from app.ai_credential_status import report_result_status
        with patch("app.ai_credential_status.report_ai_credential_status",
                   AsyncMock()) as melde:
            await report_result_status(
                {"status": "completed", "is_error": True,
                 "result": REVOKED, "is_error_text": REVOKED})
        melde.assert_awaited_once_with("auth_failed")

    async def test_der_runner_meldet_auth_failed_bevor_er_die_kennzeichen_entfernt(self):
        """Gegenleser-Fund: die Runner-Tests patchten report_result_status weg —
        ein pop VOR der Meldung waere unsichtbar geblieben, und der Zugang
        stuende in der Oberflaeche wieder auf „ok"."""
        runner = self._runner(
            {"status": "completed", "is_error": True, "result": REVOKED, "is_error_text": REVOKED},
            {"status": "completed", "is_error": True, "result": REVOKED, "is_error_text": REVOKED},
        )
        with patch("app.config.get_oauth_token", lambda: "alt"), \
             patch("app.config.wait_for_new_oauth_token", AsyncMock(return_value=None)), \
             patch("app.ai_credential_status.report_ai_credential_status",
                   AsyncMock()) as melde:
            await runner.execute_task("t1", "arbeite")
        melde.assert_awaited_once_with("auth_failed")

    async def test_gewartet_wird_auf_einen_wechsel_weg_vom_token_VOR_dem_lauf(self):
        """Gegenleser-Fund: wird der Vergleichstoken erst nach dem ersten Lauf
        gelesen, ist er schon der neue — und das Warten laeuft immer in die
        volle Frist."""
        from app.agent_runner import AgentRunner
        import app.config as cfg

        ergebnisse = iter([
            {"status": "completed", "is_error": True, "result": REVOKED, "is_error_text": REVOKED},
            {"status": "completed", "is_error": False, "result": "fertig"},
        ])
        # Der Lauf liest den Token beim Start (so spawnt der echte Lauf den CLI);
        # WAEHREND des Laufs rotiert er von alt auf neu.
        tokens = iter(["alt", "neu", "neu", "neu"])

        async def lauf(*_a, **_k):
            cfg.get_oauth_token()
            return next(ergebnisse)

        runner = AgentRunner(log_publisher=AsyncMock())
        runner._execute_task_once = lauf
        warten = AsyncMock(return_value="neu")
        with patch("app.config.get_oauth_token", lambda: next(tokens)), \
             patch("app.config.wait_for_new_oauth_token", warten), \
             patch("app.agent_runner.report_result_status", AsyncMock()):
            await runner.execute_task("t1", "arbeite")
        warten.assert_awaited_once_with("alt")

    async def test_hoechstens_ein_wiederholungslauf(self):
        """Kommt nie ein Token, bleibt es bei GENAU einer Wiederholung — der
        Orchestrator soll den dauerhaften Zugangsverlust sehen, nicht eine
        Schleife."""
        def endlos_401():
            while True:
                yield {"status": "completed", "is_error": True,
                       "result": REVOKED, "is_error_text": REVOKED}
        async def kein_token(_previous):
            # Echt an die Schleife abgeben — sonst kann wait_for eine
            # Endlosschleife nicht abbrechen und der Test haengt statt rot.
            await asyncio.sleep(0)
            return None

        from app.agent_runner import AgentRunner
        runner = AgentRunner(log_publisher=AsyncMock())
        runner._execute_task_once = AsyncMock(side_effect=endlos_401())
        with patch("app.config.get_oauth_token", lambda: "alt"), \
             patch("app.config.wait_for_new_oauth_token", kein_token), \
             patch("app.agent_runner.report_result_status", AsyncMock()):
            await asyncio.wait_for(runner.execute_task("t1", "arbeite"), timeout=5)
        self.assertEqual(runner._execute_task_once.await_count, 2)

    async def test_zugangsstatus_ok_bleibt_ok(self):
        from app.ai_credential_status import report_result_status
        with patch("app.ai_credential_status.report_ai_credential_status",
                   AsyncMock()) as melde:
            await report_result_status(
                {"status": "completed", "is_error": False, "result": "fertig"})
        melde.assert_awaited_once_with("ok")


_FAKE_CLI = r'''
import json, sys
sys.stdin.read()
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "arbeite..."}]}}))
print(json.dumps({"type": "result", "is_error": True, "subtype": "success",
                  "result": "%s", "num_turns": 40, "duration_ms": 1500000}))
''' % REVOKED

# Leeres result, Fehler im errors-Feld, davor Agententext ueber ein OAuth-Thema:
# der Fehlertext muss aus errors kommen, nicht aus dem Agententext.
_FAKE_CLI_ERRORS = r'''
import json, sys
sys.stdin.read()
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Ich fixe den 401 OAuth revoked Fall..."}]}}))
print(json.dumps({"type": "result", "is_error": True, "subtype": "success",
                  "result": "", "errors": ["Something went wrong"], "num_turns": 2}))
'''


class EchterProzessTests(unittest.IsolatedAsyncioTestCase):
    """Der einzelne Lauf reicht ``is_error`` aus dem echten Ereignisstrom durch.

    Ein Python-Prozess spielt den CLI und schreibt genau die Zeile, die im
    Betrieb kam. Ohne diese Stufe koennte ``zugang_verloren`` perfekt sein und
    trotzdem nie ``is_error`` zu sehen bekommen.
    """

    async def test_result_mit_is_error_kommt_als_is_error_an(self):
        from app import agent_runner

        echt = asyncio.create_subprocess_exec

        async def fake_exec(*cmd, **kw):
            return await echt(sys.executable, "-c", _FAKE_CLI, **kw)

        runner = agent_runner.AgentRunner(log_publisher=AsyncMock())
        with patch("app.agent_runner.asyncio.create_subprocess_exec", fake_exec), \
             patch("app.agent_runner.compose_prompt_bundle", lambda *_a, **_k: ""):
            result = await asyncio.wait_for(
                runner._execute_task_once("t1", "arbeite", model="m"), timeout=60)

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result.get("is_error"), result)
        self.assertEqual(result["result"], REVOKED)
        self.assertEqual(result["is_error_text"], REVOKED)

    async def test_fehlertext_kommt_aus_errors_nicht_aus_dem_agententext(self):
        from app import agent_runner
        from app.ai_credential_status import zugang_verloren

        echt = asyncio.create_subprocess_exec

        async def fake_exec(*cmd, **kw):
            return await echt(sys.executable, "-c", _FAKE_CLI_ERRORS, **kw)

        runner = agent_runner.AgentRunner(log_publisher=AsyncMock())
        with patch("app.agent_runner.asyncio.create_subprocess_exec", fake_exec), \
             patch("app.agent_runner.compose_prompt_bundle", lambda *_a, **_k: ""):
            result = await asyncio.wait_for(
                runner._execute_task_once("t1", "arbeite", model="m"), timeout=60)

        self.assertTrue(result["is_error"])
        self.assertEqual(result["is_error_text"], "Something went wrong")
        self.assertIn("401", result["result"])  # der Agententext ist da ...
        self.assertFalse(zugang_verloren(result))  # ... und zaehlt nicht


if __name__ == "__main__":
    unittest.main()
