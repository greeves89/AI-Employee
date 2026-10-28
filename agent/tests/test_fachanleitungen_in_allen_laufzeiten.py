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


class _DeltaOrchestrator:
    """Antwortet wie der Orchestrator MIT Deckel und Nachtrag (#1044).

    Der Vertrag, den dieser Prüfstand abbildet: der Agent meldet mit
    ``schon_gelistet``, was in dieser Unterhaltung bereits im Verlauf steht, und
    bekommt NUR die neu hinzugekommenen Einträge — oder gar nichts.

    Der Vorgänger dieses Prüfstands lieferte ohne Stichworttreffer immer dieselben
    ids und konnte das Umsortieren damit gar nicht erzeugen; der Test darauf war
    gegen den eigentlichen Fehler blind (gemessen: der ganze Block wanderte bei 11
    von 14 Nachrichten erneut in den Verlauf, Faktor 1,94 teurer als ohne Deckel).
    """

    def __init__(self, auswahl_je_auftrag: dict[str, list[int]], standard: list[int]):
        self.auswahl = auswahl_je_auftrag
        self.standard = standard
        self.rumpf: list[dict] = []

    def __call__(self, req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        self.rumpf.append(body)
        auftrag = body["auftrag"]
        schon = set(body.get("schon_gelistet") or [])
        ids = self.standard
        for stichwort, auswahl in self.auswahl.items():
            if stichwort in auftrag.lower():
                ids = auswahl
                break
        neu = [i for i in ids if i not in schon]
        resp = MagicMock()
        resp.read.return_value = json.dumps({
            "prompt": (LISTE if not schon else NACHTRAG) if neu else "",
            "anleitungen": [],
            "gelistet_ids": neu,
        }).encode("utf-8")
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp


NACHTRAG = "\n=== WEITERE FACHANLEITUNGEN (zugewiesene Skills) ===\n  • neu\n=== ENDE FACHANLEITUNGEN ===\n"


class GedeckelteListeImChatTests(unittest.TestCase):
    """Der Deckel darf im Chat die Festkosten nicht VERGRÖSSERN.

    Vorher war „Liste einmal je Unterhaltung" harmlos, weil sie vollständig war.
    Mit Deckel und je Nachricht neu berechneter Reihenfolge wechselt ein Teil der
    40 ids bei jedem Themenwechsel — den ganzen Block dann erneut zu schicken war
    schlechter als gar kein Deckel. Die Zusage lautet jetzt: jede Anleitung geht
    in einer Unterhaltung HÖCHSTENS EINMAL raus.
    """

    def _mit(self, orch):
        self.orch = orch
        self.patches = [
            patch.object(settings, "orchestrator_url", "http://orchestrator.invalid"),
            patch.object(settings, "agent_id", "agent-1"),
            patch.object(settings, "agent_token", "tok"),
            patch("app.runner_hooks.urllib.request.urlopen", orch),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def test_agent_meldet_was_schon_gelistet_wurde(self):
        # DIE tragende neue Eigenschaft: ohne diese Meldung kann der Orchestrator
        # den Nachtrag nicht bilden und schickt zwangsläufig immer alles.
        self._mit(_DeltaOrchestrator({"datev": [7, 8]}, standard=[1, 2]))
        geladen: set[str] = set()
        runner_hooks.fachanleitungen("Hallo", geladen)
        runner_hooks.fachanleitungen("Mach den DATEV-Stapel", geladen)
        self.assertEqual(self.orch.rumpf[0]["schon_gelistet"], [])
        self.assertEqual(self.orch.rumpf[1]["schon_gelistet"], [1, 2])

    def test_nachtrag_wird_angehaengt_wenn_etwas_neu_ist(self):
        self._mit(_DeltaOrchestrator({"datev": [7, 8]}, standard=[1, 2]))
        geladen: set[str] = set()
        self.assertIn(LISTE, runner_hooks.fachanleitungen("Hallo", geladen))
        self.assertIn(NACHTRAG, runner_hooks.fachanleitungen("Mach den DATEV-Stapel", geladen))

    def test_nichts_neues_schickt_nichts(self):
        # Gegenprobe, ohne die der Fix nur „immer schicken" wäre — und damit genau
        # die Festkosten zurückbrächte, die der Deckel wegnimmt.
        self._mit(_DeltaOrchestrator({}, standard=[1, 2]))
        geladen: set[str] = set()
        self.assertIn(LISTE, runner_hooks.fachanleitungen("Hallo", geladen))
        self.assertEqual("", runner_hooks.fachanleitungen("Und noch etwas", geladen))
        self.assertEqual("", runner_hooks.fachanleitungen("Und noch mehr", geladen))

    def test_jede_anleitung_hoechstens_einmal_in_der_unterhaltung(self):
        # Die Zusage selbst, über wechselnde Themen gemessen.
        self._mit(_DeltaOrchestrator(
            {"datev": [3, 4, 5], "angebot": [5, 6, 7], "lohn": [1, 7, 8]}, standard=[1, 2, 3]))
        geladen: set[str] = set()
        for nachricht in ("Hallo", "DATEV bitte", "Ein Angebot", "Lohn buchen",
                          "DATEV nochmal", "Angebot nochmal", "Hallo nochmal"):
            runner_hooks.fachanleitungen(nachricht, geladen)
        # Die ids, die der Orchestrator je Antwort als neu gemeldet hat, dürfen
        # sich über die Unterhaltung nicht wiederholen.
        alle: list[int] = []
        schon: set[int] = set()
        for body in self.orch.rumpf:
            gemeldet = set(body.get("schon_gelistet") or [])
            # monoton wachsend: nichts geht verloren, nichts kommt doppelt
            self.assertTrue(schon <= gemeldet, "schon_gelistet muss monoton wachsen")
            alle += sorted(gemeldet - schon)
            schon = gemeldet
        self.assertEqual(len(alle), len(set(alle)), f"doppelt gesendet: {alle}")
        self.assertEqual(sorted(schon), [1, 2, 3, 4, 5, 6, 7, 8])

    def test_nur_zahlen_ids_gehen_in_den_rumpf(self):
        # ``geladen`` trägt auch Anleitungs-NAMEN und den Listen-Merker. Landeten
        # die im Rumpf, wäre das Feld für den Orchestrator wertlos (dort sind die
        # ids ganze Zahlen) — und der Nachtrag fiele still auf „alles schicken".
        self._mit(_DeltaOrchestrator({}, standard=[1, 2]))
        geladen = {"Irgendeine Anleitung", runner_hooks._LISTE_GEZEIGT,
                   runner_hooks._GELISTET + "abc"}
        runner_hooks.fachanleitungen("Hallo", geladen)
        self.assertEqual(self.orch.rumpf[0]["schon_gelistet"], [])

    def test_alte_antwort_ohne_gelistet_ids_bleibt_bei_einmal(self):
        # Abwärtskompatibel: ein Orchestrator-Stand ohne das neue Feld kennt den
        # Nachtrag nicht und liefert jedes Mal die volle Liste. Dann muss hier
        # wieder „einmal je Unterhaltung" gelten.
        self._mit(_Orchestrator())
        geladen: set[str] = set()
        self.assertIn(LISTE, runner_hooks.fachanleitungen("Hallo", geladen))
        self.assertNotIn(LISTE, runner_hooks.fachanleitungen("Noch eine Nachricht", geladen))
        self.assertNotIn(LISTE, runner_hooks.fachanleitungen("Und noch eine", geladen))


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
