"""Werkzeugaufrufe bleiben im gespeicherten Verlauf lesbar (#911).

Im Markttest zeigte der Chat-Verlauf bei „Skill install" nur ``IN {}`` und bei
„Befehl ausgeführt" gar nichts. Ursache: alle drei Laufzeiten legten die Eingabe
als ``json.dumps(...)[:200]`` ab — mitten im JSON abgeschnitten, also nicht mehr
lesbar; die Oberfläche machte daraus ``{}``. Die Ausgabe wurde nie gespeichert.

Jetzt gibt es EINEN Helfer für alle Laufzeiten: Eingabe gekürzt, aber gültiges
JSON; Ausgabe gekürzt und am richtigen Eintrag (über ``tool_use_id``).
"""

import json
import unittest
from unittest.mock import AsyncMock, patch


class EingabeBleibtGueltigesJson(unittest.TestCase):

    def test_fuenf_kilobyte_eingabe_wird_parsebar_gekuerzt(self):
        from app.werkzeug_eintrag import EINGABE_GRENZE, eingabe_json

        eingabe = {"command": "echo " + "x" * 5000, "description": "Lange Ausgabe erzeugen"}
        ergebnis = eingabe_json(eingabe)

        self.assertLessEqual(len(ergebnis), EINGABE_GRENZE)
        gelesen = json.loads(ergebnis)  # darf NICHT werfen
        # Kurze Felder bleiben unangetastet, das lange beginnt wie im Original.
        self.assertEqual(gelesen["description"], "Lange Ausgabe erzeugen")
        self.assertTrue(gelesen["command"].startswith("echo xxx"))

    def test_kleine_eingabe_bleibt_unveraendert(self):
        from app.werkzeug_eintrag import eingabe_json

        eingabe = {"skill": "pdf-export", "quelle": "marktplatz"}
        self.assertEqual(json.loads(eingabe_json(eingabe)), eingabe)

    def test_viele_kurze_felder_bleiben_gueltig(self):
        """Nicht ein langer String, sondern sehr viele kleine — auch dann gültig."""
        from app.werkzeug_eintrag import EINGABE_GRENZE, eingabe_json

        eingabe = {"zeilen": [{"nr": i, "text": f"Zeile {i}"} for i in range(800)]}
        ergebnis = eingabe_json(eingabe)
        self.assertLessEqual(len(ergebnis), EINGABE_GRENZE)
        json.loads(ergebnis)

    def test_keine_eingabe_ist_ein_leeres_objekt(self):
        from app.werkzeug_eintrag import eingabe_json
        self.assertEqual(eingabe_json(None), "{}")


class AusgabeAmRichtigenEintrag(unittest.TestCase):

    def test_ergebnis_landet_beim_passenden_aufruf(self):
        from app.werkzeug_eintrag import WerkzeugListe

        liste = WerkzeugListe()
        liste.aufruf("Bash", {"command": "ls"}, "t1")
        liste.aufruf("Read", {"file_path": "/workspace/a.txt"}, "t2")
        liste.ergebnis("t2", "Inhalt von a")
        liste.ergebnis("t1", [{"type": "text", "text": "a.txt\nb.txt"}])

        eintraege = liste.liste()
        self.assertEqual(eintraege[0]["output"], "a.txt\nb.txt")
        self.assertEqual(eintraege[1]["output"], "Inhalt von a")
        self.assertEqual(eintraege[0]["tool_use_id"], "t1")

    def test_ausgabe_wird_gekuerzt(self):
        from app.werkzeug_eintrag import AUSGABE_GRENZE, WerkzeugListe

        liste = WerkzeugListe()
        liste.aufruf("Bash", {"command": "cat gross.log"}, "t1")
        liste.ergebnis("t1", "y" * 10_000)
        self.assertLessEqual(len(liste.liste()[0]["output"]), AUSGABE_GRENZE)

    def test_unbekannte_id_aendert_nichts(self):
        from app.werkzeug_eintrag import WerkzeugListe

        liste = WerkzeugListe()
        liste.aufruf("Bash", {"command": "ls"}, "t1")
        liste.ergebnis("fremd", "nicht meins")
        self.assertNotIn("output", liste.liste()[0])

    def test_derselbe_aufruf_zaehlt_nur_einmal(self):
        """Claude Code meldet denselben tool_use mitunter mehrfach."""
        from app.werkzeug_eintrag import WerkzeugListe

        liste = WerkzeugListe()
        self.assertIsNotNone(liste.aufruf("Bash", {"command": "ls"}, "t1"))
        self.assertIsNone(liste.aufruf("Bash", {"command": "ls"}, "t1"))
        self.assertEqual(len(liste.liste()), 1)

    def test_subagenten_behalten_ihre_felder(self):
        from app.werkzeug_eintrag import WerkzeugListe

        liste = WerkzeugListe()
        liste.aufruf("Agent", {"description": "Review", "subagent_type": "reviewer",
                               "prompt": "p" * 9000}, "t1")
        eintrag = liste.liste()[0]
        self.assertEqual(eintrag["subagent"]["description"], "Review")
        json.loads(eintrag["input"])

    def test_leere_liste_ist_none(self):
        """Wie bisher: ohne Werkzeuge kein Feld (``tool_calls: None``)."""
        from app.werkzeug_eintrag import WerkzeugListe
        self.assertIsNone(WerkzeugListe().liste())


class _Publisher:
    def __init__(self):
        self.events: list[tuple[str, object]] = []
        self.last_activity_at = 0.0

    async def publish_chat(self, message_id, kind, payload):
        self.events.append((kind, payload))

    async def publish(self, task_id, kind, payload):
        self.events.append((kind, payload))

    def notiere_fortschritt(self, *_a):
        pass


class _Ereignis:
    def __init__(self, type, **kw):
        self.type = type
        self.text = kw.get("text", "")
        self.tool_id = kw.get("tool_id", "")
        self.tool_name = kw.get("tool_name", "")
        self.tool_input = kw.get("tool_input", {})
        self.input_tokens = kw.get("input_tokens", 1)
        self.output_tokens = kw.get("output_tokens", 1)


class _Anbieter:
    """Erst ein Werkzeugaufruf mit 5 KB Eingabe, dann eine Schlussantwort."""

    def __init__(self):
        self.zug = 0
        self.reasoning_effort = ""

    def stream_completion(self, messages, tools=None):
        self.zug += 1
        zug = self.zug

        async def gen():
            if zug == 1:
                yield _Ereignis("tool_call", tool_id="c1", tool_name="bash",
                                tool_input={"command": "echo " + "z" * 5000})
            else:
                yield _Ereignis("text_delta", text="Fertig.")
            yield _Ereignis("done")

        return gen()

    async def close(self):
        pass


class DreiLaufzeitenGleich(unittest.IsolatedAsyncioTestCase):
    """Parität: Claude Code, Custom-LLM-Chat und Custom-LLM-Auftrag liefern
    dieselbe Form — gültige Eingabe + Ausgabe am Eintrag."""

    def _pruefe(self, tool_calls):
        self.assertTrue(tool_calls, "keine Werkzeugliste im Ergebnis")
        eintrag = tool_calls[0]
        json.loads(eintrag["input"])
        self.assertIn("Ausgabe des Befehls", eintrag["output"])

    def test_claude_code_strom_leser(self):
        from app.chat_handler import StromLeser

        leser = StromLeser()
        leser.ereignis({"type": "assistant", "parent_tool_use_id": None, "message": {
            "id": "m1", "content": [{"type": "tool_use", "id": "c1", "name": "Bash",
                                     "input": {"command": "echo " + "z" * 5000}}]}})
        leser.ereignis({"type": "user", "parent_tool_use_id": None, "message": {
            "content": [{"type": "tool_result", "tool_use_id": "c1",
                         "content": "Ausgabe des Befehls"}]}})
        self._pruefe(leser.werkzeuge.liste())

    async def test_custom_llm_chat(self):
        from app.llm_chat_handler import LLMChatHandler
        from app.providers.base import ChatMessage

        pub = _Publisher()
        h = LLMChatHandler(log_publisher=pub)
        h._context_window = 1_000_000
        h._history = [ChatMessage(role="system", content="S")]
        h._tool_executor.execute = AsyncMock(return_value="Ausgabe des Befehls")
        with patch.object(h, "_get_provider", return_value=_Anbieter()), \
             patch.object(h, "_get_tools", new=AsyncMock(return_value=None)), \
             patch("app.llm_chat_handler.report_result_status", new=AsyncMock()):
            ergebnis = await h.handle_message("m1", "mach was")
        self._pruefe(ergebnis["tool_calls"])

    async def test_custom_llm_auftrag(self):
        from app.llm_runner import LLMRunner

        pub = _Publisher()
        r = LLMRunner(log_publisher=pub)
        r._tool_executor.execute = AsyncMock(return_value="Ausgabe des Befehls")
        # Der Prompt-Aufbau liest /workspace und fragt den Orchestrator — hier
        # nicht die Frage, deshalb leer.
        leer = {name: patch(f"app.llm_runner.{name}", return_value="") for name in (
            "get_identity_context", "get_memory_preload", "get_skills_context",
            "get_mounts_context", "get_marketplace_skill_suggestions")}
        for p in leer.values():
            p.start()
        self.addCleanup(patch.stopall)
        with patch.object(r, "_get_provider", return_value=_Anbieter()), \
             patch.object(r, "_get_tools", new=AsyncMock(return_value=None)), \
             patch("app.llm_runner.report_result_status", new=AsyncMock()):
            ergebnis = await r.execute_task("t1", "mach was", lightweight=True)
        self._pruefe(ergebnis["tool_calls"])

    async def test_codex(self):
        """Codex lieferte bisher gar keine Liste — der Verlauf hing allein am
        Mitschnitt des Orchestrators und hatte keine Ausgaben."""
        import os
        import tempfile

        from app.codex_runner import CodexAgentRunner
        from tests.test_codex_exit_preserves_partial_result import _FakeProcess

        def zeile(ereignis):
            return (json.dumps(ereignis) + "\n").encode()

        befehl = {"id": "c1", "type": "command_execution", "command": "echo " + "z" * 5000}
        zeilen = [
            zeile({"type": "item.started", "item": dict(befehl, status="in_progress")}),
            zeile({"type": "item.completed", "item": dict(
                befehl, status="completed", aggregated_output="Ausgabe des Befehls", exit_code=0)}),
            zeile({"type": "item.completed", "item": {"type": "agent_message", "text": "Fertig."}}),
            zeile({"type": "turn.completed", "usage": {}}),
        ]
        pub = _Publisher()
        runner = CodexAgentRunner(pub)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"CODEX_HOME": tmp}), \
                patch("app.codex_runner._codex_auth_problem", return_value=None), \
                patch("app.codex_runner.codex_auth_sync.push_if_rotated", AsyncMock(return_value=False)), \
                patch("app.codex_runner.asyncio.create_subprocess_exec",
                      AsyncMock(return_value=_FakeProcess(zeilen, [], 0))):
            ergebnis = await runner._run_codex("m1", "prompt", "model", stream="chat")
        self._pruefe(ergebnis["tool_calls"])


if __name__ == "__main__":
    unittest.main()
