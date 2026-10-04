"""Agenten kennen Datum und Wochentag — in jeder Laufzeit (#905).

Im Markttest nannten Agenten „2025" statt 2026 und falsche Wochentage
(„Mo. 06.10."). In keiner Laufzeit stand Datum oder Uhrzeit im Kontext; das
Modell riet aus seinem Trainingsstand.

Jetzt traegt jede Chat-Nachricht (gemeinsame Strecke im ChatConsumer: Web,
Telegram, alle Laufzeiten) und jeder Auftrag (alle drei Runner) eine Zeile wie
``[Jetzt: Sonntag, 4. Oktober 2026, 14:32 Uhr, Europe/Berlin]``.
"""

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

#: 04.10.2026, 12:32 UTC = 14:32 Uhr in Berlin (Sommerzeit).
_FEST = datetime(2026, 10, 4, 12, 32, tzinfo=timezone.utc)


class ZeitkontextTests(unittest.TestCase):

    def test_sonntag_am_vierten_oktober_2026(self):
        from app.runner_hooks import zeitkontext
        with patch.dict(os.environ, {"TZ": "Europe/Berlin"}):
            self.assertEqual(zeitkontext(_FEST),
                             "[Jetzt: Sonntag, 4. Oktober 2026, 14:32 Uhr, Europe/Berlin]")

    def test_zeitzone_kommt_aus_tz(self):
        from app.runner_hooks import zeitkontext
        with patch.dict(os.environ, {"TZ": "America/New_York"}):
            self.assertEqual(zeitkontext(_FEST),
                             "[Jetzt: Sonntag, 4. Oktober 2026, 08:32 Uhr, America/New_York]")

    def test_ohne_tz_gilt_berlin(self):
        from app.runner_hooks import zeitkontext
        umgebung = {k: v for k, v in os.environ.items() if k != "TZ"}
        with patch.dict(os.environ, umgebung, clear=True):
            self.assertIn("Europe/Berlin", zeitkontext(_FEST))

    def test_unbekannte_zeitzone_faellt_auf_berlin_zurueck(self):
        from app.runner_hooks import zeitkontext
        with patch.dict(os.environ, {"TZ": "Mars/Olympus"}):
            self.assertEqual(zeitkontext(_FEST),
                             "[Jetzt: Sonntag, 4. Oktober 2026, 14:32 Uhr, Europe/Berlin]")

    def test_ohne_uhr_gilt_jetzt(self):
        from app.runner_hooks import zeitkontext
        self.assertIn(str(datetime.now().year), zeitkontext())


def _fest_patch():
    """Feste Uhr fuer ``zeitkontext`` ueberall dort, wo es aufgerufen wird."""
    from app import runner_hooks
    original = runner_hooks.zeitkontext
    return patch("app.runner_hooks.zeitkontext", lambda jetzt=None: original(_FEST))


class ChatStreckeTests(unittest.TestCase):
    """Die gemeinsame Chat-Strecke gilt fuer Web, Telegram und alle Laufzeiten."""

    def _consumer(self):
        from app.chat_consumer import ChatConsumer
        return ChatConsumer.__new__(ChatConsumer)

    class _Handler:
        def __init__(self, session_id):
            self.session_id = session_id

    def _ruhig(self):
        return [
            patch("app.runner_hooks.get_approval_rules_prefix", lambda: ""),
            patch("app.runner_hooks.get_skills_context", lambda: ""),
            patch("app.runner_hooks.get_marketplace_skill_suggestions", lambda t: ""),
            patch.dict(os.environ, {"TZ": "Europe/Berlin"}),
            _fest_patch(),
        ]

    def _mit(self, aufruf):
        patches = self._ruhig()
        for p in patches:
            p.start()
        try:
            return aufruf()
        finally:
            for p in reversed(patches):
                p.stop()

    def test_jede_nachricht_traegt_die_zeit(self):
        consumer = self._consumer()
        for handler in (self._Handler(None), self._Handler("sess-1")):
            with self.subTest(neu=handler.session_id is None):
                out = self._mit(lambda: consumer._prepare_text("Welcher Tag ist heute?", None,
                                                               "webapp", handler))
                self.assertIn("[Jetzt: Sonntag, 4. Oktober 2026", out)
                self.assertIn("Welcher Tag ist heute?", out)

    def test_telegram_traegt_die_zeit(self):
        consumer = self._consumer()
        out = self._mit(lambda: consumer._prepare_text(
            "hi", {"chat_id": "1", "first_name": "A", "message_id": 2}, "telegram",
            self._Handler("sess-1")))
        self.assertIn("[Jetzt: Sonntag, 4. Oktober 2026", out)

    def test_neue_sitzung_nach_laengenfehler_traegt_die_zeit(self):
        consumer = self._consumer()
        out = self._mit(lambda: consumer._fresh_session_text("hallo", None, "webapp"))
        self.assertIn("[Jetzt: Sonntag, 4. Oktober 2026", out)


class _Publisher:
    def __init__(self):
        self.last_activity_at = 0.0

    async def publish(self, *a, **k):
        pass

    async def publish_chat(self, *a, **k):
        pass

    def notiere_fortschritt(self, *_a):
        pass


class _Leer:
    async def read(self, _n=-1):
        return b""

    async def readline(self):
        return b""


class _Prozess:
    """Ein CLI-Prozess, der sofort ohne Ausgabe endet."""

    def __init__(self):
        self.stdout = _Leer()
        self.stderr = _Leer()
        self.stdin = None
        self.returncode = 0

    async def wait(self):
        return 0


class AuftragsRunnerTests(unittest.IsolatedAsyncioTestCase):
    """Jeder der drei Auftrags-Runner gibt dem Modell die Zeit mit."""

    def setUp(self):
        for p in (patch.dict(os.environ, {"TZ": "Europe/Berlin"}), _fest_patch()):
            p.start()
            self.addCleanup(p.stop)

    async def test_claude_code(self):
        from app.agent_runner import AgentRunner
        gesehen = {}

        async def abfangen(proc, prompt):
            gesehen["prompt"] = prompt

        runner = AgentRunner(log_publisher=_Publisher())
        with patch("app.agent_runner.compose_prompt_bundle", return_value=""), \
             patch("app.agent_runner.feed_prompt_via_stdin", abfangen), \
             patch("app.agent_runner.asyncio.create_subprocess_exec",
                   AsyncMock(return_value=_Prozess())):
            try:
                await runner.execute_task("t1", "Was ist heute?")
            except Exception:  # noqa: BLE001 — der Prompt ist das Pruefobjekt
                pass
        self.assertIn("[Jetzt: Sonntag, 4. Oktober 2026", gesehen.get("prompt", ""))

    async def test_codex(self):
        from app.codex_runner import CodexAgentRunner
        gesehen = {}

        async def abfangen(self_, task_id, prompt, model, stream="task"):
            gesehen["prompt"] = prompt
            return {"status": "completed", "result": ""}

        runner = CodexAgentRunner(log_publisher=_Publisher())
        with patch("app.codex_runner.compose_prompt_bundle", return_value=""), \
             patch.object(CodexAgentRunner, "_run_codex", abfangen), \
             patch("app.codex_runner.report_result_status", AsyncMock()):
            try:
                await runner.execute_task("t1", "Was ist heute?")
            except Exception:  # noqa: BLE001
                pass
        self.assertIn("[Jetzt: Sonntag, 4. Oktober 2026", gesehen.get("prompt", ""))

    async def test_custom_llm(self):
        from app.llm_runner import LLMRunner
        gesehen = {}

        class _Anbieter:
            reasoning_effort = ""

            def stream_completion(self, messages, tools=None):
                gesehen["nachrichten"] = [m.content for m in messages]

                async def gen():
                    raise RuntimeError("Ende")
                    yield  # pragma: no cover

                return gen()

            async def close(self):
                pass

        runner = LLMRunner(log_publisher=_Publisher())
        leer = [patch(f"app.llm_runner.{n}", return_value="") for n in (
            "get_identity_context", "get_memory_preload", "get_skills_context",
            "get_mounts_context", "get_marketplace_skill_suggestions",
            "get_approval_rules_prefix", "get_improvement_context")]
        for p in leer:
            p.start()
            self.addCleanup(p.stop)
        for leicht in (True, False):
            gesehen.clear()
            with self.subTest(lightweight=leicht), \
                    patch.object(runner, "_get_provider", return_value=_Anbieter()), \
                    patch.object(runner, "_get_tools", new=AsyncMock(return_value=None)), \
                    patch("app.llm_runner.report_result_status", AsyncMock()), \
                    patch("app.llm_runner.report_ai_credential_status", AsyncMock(), create=True):
                try:
                    await runner.execute_task("t1", "Was ist heute?", lightweight=leicht)
                except Exception:  # noqa: BLE001
                    pass
                nutzer = "\n".join(str(c) for c in gesehen.get("nachrichten", [])[1:])
                self.assertIn("[Jetzt: Sonntag, 4. Oktober 2026", nutzer)


if __name__ == "__main__":
    unittest.main()
