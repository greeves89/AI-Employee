"""Zugewiesene Fachanleitungen erreichen JEDE Laufzeit — im Auftrag und im Chat.

Befund aus der Abnahme von v1.362.1: Ein Claude-Code-Agent der Vorlage
„Buchhaltung“ schrieb im Chat einen DATEV-Buchungsstapel mit Nettobetrag bei
BU 9, obwohl der zugewiesene Skill die Brutto-Regel samt Prüfschritt enthält.
Nachgemessen:

* Chat (Claude Code, Codex): die zugewiesenen Skills tauchten gar nicht auf —
  nur Dateien unter ``/workspace/.claude/skills`` und eine Stichwortsuche.
* Custom-LLM-Aufträge: ``get_skill_preload`` war importiert, aber nie gerufen.
* Aufträge (Claude Code, Codex): Liste ja, aber ohne ladbare ID.

Jetzt holt jede Laufzeit über ``runner_hooks.fachanleitungen`` denselben Block
vom Orchestrator: Regel + Liste einmal je Unterhaltung, die zum Auftrag
passende Anleitung vollständig — und dieselbe Anleitung nicht zweimal.
"""

import json
import unittest
import urllib.error
from unittest.mock import AsyncMock, MagicMock, patch

from app import runner_hooks
from app.config import settings

LISTE = "=== DEINE FACHANLEITUNGEN (Test) ==="
ANLEITUNG = "=== FACHANLEITUNG FÜR DIESEN AUFTRAG: buchhaltung-vorkontieren ==="


class _Orchestrator:
    """Antwortet wie ``POST /skills/agent/fachanleitungen``: passt „DATEV“, kommt die Anleitung mit."""

    def __init__(self):
        self.auftraege: list[str] = []

    def __call__(self, req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        self.auftraege.append(body["auftrag"])
        antwort = {"prompt": LISTE, "anleitungen": []}
        if "datev" in body["auftrag"].lower():
            antwort["anleitungen"] = [{"id": 7, "name": "buchhaltung-vorkontieren", "text": ANLEITUNG}]
        resp = MagicMock()
        resp.read.return_value = json.dumps(antwort).encode("utf-8")
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp


class _Umgebung(unittest.TestCase):
    def setUp(self):
        self.orch = _Orchestrator()
        for p in (
            patch.object(settings, "orchestrator_url", "http://orchestrator.invalid"),
            patch.object(settings, "agent_id", "agent-1"),
            patch.object(settings, "agent_token", "tok"),
            patch("app.runner_hooks.urllib.request.urlopen", self.orch),
        ):
            p.start()
            self.addCleanup(p.stop)


class FachanleitungenTests(_Umgebung):
    def test_liste_einmal_anleitung_einmal(self):
        geladen: set[str] = set()
        erst = runner_hooks.fachanleitungen("Hallo", geladen)
        self.assertIn(LISTE, erst)
        self.assertNotIn(ANLEITUNG, erst)

        zweit = runner_hooks.fachanleitungen("Mach mir den DATEV-Buchungsstapel", geladen)
        self.assertIn(ANLEITUNG, zweit)
        self.assertNotIn(LISTE, zweit)

        dritt = runner_hooks.fachanleitungen("Noch eine DATEV-Zeile bitte", geladen)
        self.assertEqual("", dritt)
        self.assertEqual(["Hallo", "Mach mir den DATEV-Buchungsstapel", "Noch eine DATEV-Zeile bitte"],
                         self.orch.auftraege)

    def test_ohne_orchestrator_nichts_und_beim_naechsten_mal_wieder_versuchen(self):
        geladen: set[str] = set()
        with patch("app.runner_hooks.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("weg")):
            self.assertEqual("", runner_hooks.fachanleitungen("DATEV", geladen))
        self.assertIn(LISTE, runner_hooks.fachanleitungen("DATEV", geladen))

    def test_auftrag_bekommt_liste_und_anleitung(self):
        text = runner_hooks.get_skill_preload("Erstelle den DATEV-Export")
        self.assertIn(LISTE, text)
        self.assertIn(ANLEITUNG, text)


class ChatTests(_Umgebung):
    """Alle drei Laufzeiten bekommen den Block über ``ChatConsumer._prepare_text``."""

    def setUp(self):
        super().setUp()
        for name in ("get_approval_rules_prefix", "get_skills_context", "get_marketplace_skill_suggestions"):
            p = patch(f"app.runner_hooks.{name}", return_value="")
            p.start()
            self.addCleanup(p.stop)

    def _gespraech(self, modus: str):
        from app.chat_consumer import ChatConsumer

        consumer = ChatConsumer(agent_id="agent-1")
        handler = MagicMock(spec=["session_id"]) if modus == "claude_code" else MagicMock(spec=[])
        if modus == "claude_code":
            handler.session_id = None
        with patch("app.chat_consumer.settings") as s:
            s.agent_mode = modus
            texte = [consumer._prepare_text("Hallo, wer bist du?", None, "webapp", handler)]
            if modus == "claude_code":
                handler.session_id = "sess-1"
            texte.append(consumer._prepare_text("Bitte einen DATEV-Buchungsstapel", None, "webapp", handler))
            texte.append(consumer._prepare_text("Und noch eine DATEV-Zeile", None, "webapp", handler))
        return consumer, handler, texte

    def test_jede_laufzeit(self):
        for modus in ("claude_code", "codex_cli", "custom_llm"):
            with self.subTest(modus=modus):
                _, _, (erst, zweit, dritt) = self._gespraech(modus)
                self.assertIn(LISTE, erst)
                self.assertNotIn(ANLEITUNG, erst)
                self.assertIn(ANLEITUNG, zweit)
                self.assertNotIn(LISTE, zweit)
                self.assertNotIn(ANLEITUNG, dritt)
                self.assertIn("Bitte einen DATEV-Buchungsstapel", zweit)

    def test_neuer_chat_beginnt_von_vorn(self):
        import asyncio

        consumer, handler, _ = self._gespraech("custom_llm")
        consumer._handlers["webapp"] = handler
        consumer.redis = AsyncMock()
        asyncio.run(consumer._reset_handler("webapp"))
        with patch("app.chat_consumer.settings") as s:
            s.agent_mode = "custom_llm"
            neu = consumer._prepare_text("Wieder DATEV bitte", None, "webapp", handler)
        self.assertIn(LISTE, neu)
        self.assertIn(ANLEITUNG, neu)

    def test_neuer_anlauf_nach_laengenfehler_bekommt_alles(self):
        from app.chat_consumer import ChatConsumer

        with patch("app.chat_consumer.settings") as s:
            s.agent_mode = "claude_code"
            text = ChatConsumer(agent_id="agent-1")._fresh_session_text("DATEV bitte", None, "webapp")
        self.assertIn(LISTE, text)
        self.assertIn(ANLEITUNG, text)


class AuftragsLaufzeitenTests(unittest.IsolatedAsyncioTestCase):
    """Claude Code und Codex über das gemeinsame Bündel, Custom-LLM direkt."""

    def setUp(self):
        leer = ("get_onboarding_context", "get_disk_incident_context", "get_memory_preload",
                "get_user_feedback", "get_skills_context", "get_mounts_context",
                "get_marketplace_skill_suggestions", "get_improvement_context")
        for name in leer:
            p = patch(f"app.runner_hooks.{name}", return_value="")
            p.start()
            self.addCleanup(p.stop)
        p = patch("app.runner_hooks.get_skill_preload", side_effect=lambda auftrag="": f"FACH[{auftrag}]")
        p.start()
        self.addCleanup(p.stop)

    def test_buendel_gibt_den_auftrag_weiter(self):
        for leicht in (True, False):
            with self.subTest(lightweight=leicht):
                self.assertIn("FACH[DATEV-Export bitte]",
                              runner_hooks.compose_prompt_bundle("DATEV-Export bitte", leicht))

    async def test_custom_llm_auftrag(self):
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

        class _Publisher:
            last_activity_at = 0.0

            async def publish(self, *a, **k):
                pass

            async def publish_chat(self, *a, **k):
                pass

            def notiere_fortschritt(self, *_a):
                pass

        runner = LLMRunner(log_publisher=_Publisher())
        for name in ("get_identity_context", "get_approval_rules_prefix", "get_memory_preload",
                     "get_skills_context", "get_mounts_context", "get_marketplace_skill_suggestions",
                     "get_improvement_context"):
            p = patch(f"app.llm_runner.{name}", return_value="")
            p.start()
            self.addCleanup(p.stop)
        p = patch("app.llm_runner.get_skill_preload", side_effect=lambda auftrag="": f"FACH[{auftrag}]")
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
                    await runner.execute_task("t1", "DATEV-Export bitte", lightweight=leicht)
                except Exception:  # noqa: BLE001 — der Prompt ist das Prüfobjekt
                    pass
                alles = "\n".join(str(c) for c in gesehen.get("nachrichten", []))
                self.assertIn("FACH[DATEV-Export bitte]", alles)


if __name__ == "__main__":
    unittest.main()
