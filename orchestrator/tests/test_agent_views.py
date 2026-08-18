"""Ansichten: der Agent zeigt etwas und wartet auf die Wahl des Nutzers.

Idee des Nutzers (18.08.2026): „Wie kann ich unser UI so anpassen, dass wir auch
Bilder bearbeiten koennten … dem User werden direkt Views vorgegeben wenn man mit
dem Agent interagiert, sowohl im Chat als auch im Voice Mode."

Vorhanden war: ``present_image`` (einweg, der Nutzer kann nur schauen) und
``request_approval`` (zweiweg, aber nur Text). Es fehlte die interaktive Ansicht
MIT Rueckweg.

Gebaut als **Erweiterung der Rueckfrage**, nicht als zweiter Weg daneben:
dieselbe Zeile, dasselbe Anhalten des Agenten, derselbe Rueckweg ueber
``user_response``. Damit funktioniert eine Ansicht ohne weiteres Zutun in der
Ablage, auf Telegram und auf dem Telefon — dort eben als Wortoptionen.

Die zwei Punkte, an denen so etwas gefaehrlich wird, und wie sie abgesichert
sind:

1. **Der Agent darf kein Markup liefern.** Ein Modell, das HTML in die
   Oberflaeche schreibt, ist ein Einfallstor mit Zwischenschritt — zumal sein
   Rohstoff (Webseiten, Dateien, Mails) von aussen kommt. Er waehlt einen NAMEN
   aus einer Liste; gezeichnet wird im Frontend.
2. **Eine Ansicht darf keine Sackgasse sein.** Wer sie nicht sehen kann
   (Telegram, Telefon, reine Stimme), muss trotzdem antworten koennen — sonst
   wartet der Agent bis zur Zeitgrenze.
"""

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.api import approvals as api
from app.models.agent import Agent, AgentState
from app.models.audit_log import AuditLog
from app.models.command_approval import CommandApproval
from app.models.notification import Notification
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

ROOT = Path(__file__).resolve().parents[2]


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


class TheAgentCannotInventAViewTests(unittest.IsolatedAsyncioTestCase):
    """Nur was die Oberflaeche kennt, kommt durch."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, CommandApproval, Notification, AuditLog):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(Agent(id="a1", name="Marketing", state=AgentState.RUNNING,
                         user_id="u1", config={}))
            await db.commit()
        self.redis = AsyncMock()
        self.redis.client = AsyncMock()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _anfragen(self, view):
        body = api.ApprovalRequest(
            question="Welches Motiv nehmen wir?",
            options=["Variante A", "Variante B"],
            view=view,
        )
        async with self.Session() as db:
            with patch.object(api, "_get_redis", return_value=self.redis), \
                 patch.object(api, "_push_ios_for_agent", new=AsyncMock()):
                res = await api.request_approval(body, agent_auth={"agent_id": "a1"}, db=db)
            zeile = await db.get(CommandApproval, int(res["approval_id"]))
            return (zeile.meta or {}).get("view")

    async def test_a_known_view_is_kept(self):
        gespeichert = await self._anfragen(
            {"name": "image_choice", "data": {"images": [{"path": "/workspace/a.png"}]}})
        self.assertEqual(gespeichert["name"], "image_choice")
        self.assertEqual(gespeichert["data"]["images"][0]["path"], "/workspace/a.png")

    async def test_an_invented_view_is_dropped(self):
        """Ein unbekannter Name wuerde im Frontend zu einer leeren Flaeche —
        der Agent haette angehalten und niemand koennte antworten."""
        self.assertIsNone(await self._anfragen({"name": "beliebiges_html", "data": {}}))

    async def test_markup_is_not_a_view_name(self):
        self.assertIsNone(await self._anfragen(
            {"name": "<img src=x onerror=alert(1)>", "data": {}}))

    async def test_an_oversized_payload_is_dropped(self):
        """Bilder gehoeren als Pfad hinein, nicht als Inhalt: die Nutzlast liegt
        in derselben Zeile wie die Freigabe und geht ueber denselben
        Redis-Kanal."""
        self.assertIsNone(await self._anfragen(
            {"name": "image_choice", "data": {"images": [{"path": "x" * 9000}]}}))

    async def test_the_question_survives_a_dropped_view(self):
        """Verworfene Ansicht heisst nicht verworfene Rueckfrage — sonst steht
        der Agent still und der Nutzer sieht nichts."""
        body = api.ApprovalRequest(
            question="Welches Motiv?", options=["A", "B"],
            view={"name": "gibtsnicht", "data": {}},
        )
        async with self.Session() as db:
            with patch.object(api, "_get_redis", return_value=self.redis), \
                 patch.object(api, "_push_ios_for_agent", new=AsyncMock()):
                res = await api.request_approval(body, agent_auth={"agent_id": "a1"}, db=db)
            zeile = await db.get(CommandApproval, int(res["approval_id"]))
        self.assertEqual((zeile.meta or {})["options"], ["A", "B"])

    async def test_the_view_reaches_the_ui(self):
        await self._anfragen({"name": "image_choice", "data": {"images": []}})
        async with self.Session() as db:
            zeile = await db.get(CommandApproval, 1)
        self.assertEqual(api._approval_to_dict(zeile)["view"]["name"], "image_choice")


class AViewIsNeverADeadEndTests(unittest.TestCase):
    """Telegram, Telefon und reine Stimme koennen nichts zeichnen."""

    CLIENT = (ROOT / "agent/app/tools/api_client.py").read_text()
    MCP = (ROOT / "agent/mcp/notification-server.mjs").read_text()
    DEFS = (ROOT / "agent/app/tools/definitions.py").read_text()

    def test_the_tool_demands_words_as_well(self):
        block = self.DEFS.split('"name": "present_view"', 1)[1][:2000]
        self.assertIn("Telegram", block)

    def test_the_custom_llm_runtime_derives_options_when_missing(self):
        block = self.CLIENT.split("async def present_view", 1)[1][:1600]
        self.assertIn("label", block)

    def test_the_mcp_runtime_derives_them_too(self):
        """Sonst haette dieselbe Frage je nach Laufzeit einen anderen Ausgang."""
        block = self.MCP.split('case "present_view":', 1)[1][:1200]
        self.assertIn("label", block)


class TheSameWayAsAnApprovalTests(unittest.TestCase):
    """Eine zweite Mechanik daneben wuerde beim ersten Umbau auseinanderlaufen."""

    CLIENT = (ROOT / "agent/app/tools/api_client.py").read_text()
    MCP = (ROOT / "agent/mcp/notification-server.mjs").read_text()

    def test_the_custom_llm_runtime_shares_the_waiting_loop(self):
        self.assertIn("_fragen_und_warten", self.CLIENT)
        block = self.CLIENT.split("async def present_view", 1)[1]
        self.assertIn("await self._fragen_und_warten(body, params)", block)

    def test_the_mcp_runtime_shares_the_handler(self):
        self.assertIn('case "present_view":\n    case "request_approval": {', self.MCP)

    def test_showing_a_view_is_not_reported_as_high_risk(self):
        """Sie fuehrt nichts aus. Alles als hohes Risiko zu melden stumpft die
        Dringlichkeitsstufen ab, bis niemand mehr hinsieht."""
        block = self.MCP.split('case "present_view":', 1)[1][:1400]
        self.assertIn('istAnsicht ? "low" : "high"', block)


class EveryRuntimeHasItTests(unittest.TestCase):
    """Harness-Paritaet: eine Faehigkeit, die nur eine Laufzeit hat, ist nicht
    gebaut."""

    def test_codex_and_custom_llm(self):
        from app.core.agent_toolset import DEFINITION_TOOLS
        self.assertIn("present_view", DEFINITION_TOOLS)

    def test_claude_code_via_mcp(self):
        from app.core.agent_toolset import MCP_SERVER_TOOLS
        self.assertIn("present_view", MCP_SERVER_TOOLS["notification"])

    def test_it_is_never_blocked_by_the_autonomy_whitelist(self):
        """Eine Ansicht IST eine Rueckfrage — sie darf so wenig an einer
        Freigabe haengen wie ``request_approval`` selbst."""
        src = (ROOT / "agent/app/tools/executor.py").read_text()
        block = src.split("ALWAYS_ALLOWED_TOOLS", 1)[1].split("})", 1)[0]
        self.assertIn('"present_view"', block)


class TheUiDrawsItTests(unittest.TestCase):
    REGISTRY = (ROOT / "frontend/src/components/agents/agent-views.tsx").read_text()
    MODAL = (ROOT / "frontend/src/components/agents/approval-modal.tsx").read_text()
    VOICE = (ROOT / "frontend/src/components/agents/voice-session.tsx").read_text()

    def test_the_registry_matches_the_server_whitelist(self):
        """Zwei Listen, die auseinanderlaufen, sind in diesem Projekt schon
        zweimal teuer geworden."""
        import re
        namen = set(re.findall(r"^  (\w+): \w+,$", self.REGISTRY.split(
            "export const AGENT_VIEWS", 1)[1].split("};", 1)[0], re.MULTILINE))
        self.assertEqual(namen, api.ERLAUBTE_ANSICHTEN)

    def test_chat_renders_it(self):
        self.assertIn("<AgentView", self.MODAL)

    def test_voice_renders_it(self):
        """Im Sprachmodus zahlt sie sich am meisten aus: zeigen statt
        beschreiben."""
        self.assertIn("<AgentView", self.VOICE)

    def test_voice_no_longer_swallows_options(self):
        """Bis 2026-08-18 standen dort fest ``options[0]`` und ``options[1]`` —
        bei vier Antworten gingen zwei verloren, und im Sprachmodus ist dieses
        Feld die einzige Stelle zum Antworten."""
        self.assertNotIn("pendingApproval.options?.[1]", self.VOICE)
        block = self.VOICE.split("pendingApproval.options", 1)[1][:400]
        self.assertIn(".map(", block)

    def test_the_view_never_replaces_the_words(self):
        """Auch im Browser bleiben die Wortoptionen stehen — wer lieber tippt
        oder die Ansicht nicht laden kann, muss antworten koennen."""
        nach_ansicht = self.MODAL.split("<AgentView", 1)[1]
        self.assertIn("request.options.map(", nach_ansicht)

    def test_a_missing_image_says_so(self):
        """Ein leeres Feld sieht aus wie ein Fehler und der Nutzer antwortet gar
        nicht — waehrend der Agent wartet."""
        self.assertIn("nicht gefunden", self.REGISTRY)


if __name__ == "__main__":
    unittest.main()
