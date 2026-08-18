"""Eine Rueckfrage mit Antwortmoeglichkeiten muss beantwortbar sein.

Nutzerbericht vom 18.08.2026, mit Bildschirmfoto: ein Agent fragte
„Was bedeutet 'Poros' bei <Projekt>?" und bot vier Antworten an. Der Nutzer:
„muss ich die nicht anklicken koennen?"

Konnte er nicht. Die Optionen standen als ``<span>`` da — reiner Text. Zur
Auswahl standen nur „Approve" und „Deny", und ``approve`` schrieb in
``user_response`` grundsaetzlich ``Approved by <mail>``. Der Agent erfuhr also
nie, WELCHE der vier Antworten gemeint war, und musste im naechsten Zug erneut
fragen. Wer wirklich antworten wollte, musste ABLEHNEN und die Antwort ins
Begruendungsfeld tippen — bei einer harmlosen Verstaendnisfrage.

**Ueber Telegram ging das Waehlen die ganze Zeit** (``agent_bot`` schreibt die
gewaehlte Option in ``user_response`` und veroeffentlicht sie als ``reason``).
Auch der Custom-LLM-Weg liest ``user_response`` seit jeher als die Wahl. Nur die
Weboberflaeche und der MCP-Weg hatten es nie bekommen — eine Luecke in der
Paritaet der Laufzeiten, keine fehlende Funktion.

Die Korrektur benutzt bewusst dieselben Felder wie der Telegram-Weg, damit es
nicht zwei Mechaniken nebeneinander gibt.
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.api import approvals as api
from app.models.agent import Agent, AgentState
from app.models.audit_log import AuditLog
from app.models.command_approval import ApprovalStatus, CommandApproval
from app.models.notification import Notification
from app.models.user import UserRole
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


def _admin():
    return SimpleNamespace(id="u1", role=UserRole.ADMIN, email="admin@example.test")


class AnsweringAQuestionTests(unittest.IsolatedAsyncioTestCase):
    #: Woertlich die Frage und die Antwortmoeglichkeiten aus dem Bericht,
    #: ohne den Projektnamen.
    FRAGE = "Was bedeutet 'Poros'? Ich finde dazu nirgends eine Definition."
    OPTIONEN = (
        "Polar (Fitness-API/Wearable-Daten)",
        "Ein anderer Dienst/eine andere App (bitte im Chat nennen)",
        "War ein Verhoerer/Tippfehler, es gibt keine Poros-Integration",
        "Brauche mehr Zeit, spaeter klaeren",
    )

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, CommandApproval, Notification, AuditLog):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(Agent(id="a1", name="Testagent", state=AgentState.RUNNING,
                         user_id="u1", config={}))
            db.add(CommandApproval(
                agent_id="a1", command="ask", description=self.FRAGE,
                risk_level="high", status=ApprovalStatus.PENDING,
                meta={"question": self.FRAGE, "options": list(self.OPTIONEN)},
            ))
            await db.commit()
        self.redis = AsyncMock()
        self.redis.client = AsyncMock()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _approve(self, db, antwort=None):
        body = api.ApprovalAnswer(answer=antwort) if antwort is not None else None
        with patch.object(api, "_get_redis", return_value=self.redis):
            return await api.approve_request("1", body=body, user=_admin(), db=db)

    async def _row(self):
        async with self.Session() as db:
            return await db.get(CommandApproval, 1)

    def _agentenkanal(self):
        """Was auf dem Kanal landet, auf dem der Agent wartet."""
        for call in self.redis.client.publish.await_args_list:
            kanal, nutzlast = call.args
            if kanal == "approval:1":
                return json.loads(nutzlast)
        return None

    async def test_the_chosen_option_is_what_the_agent_reads(self):
        """``user_response`` ist der Kanal — genau wie beim Telegram-Weg."""
        async with self.Session() as db:
            await self._approve(db, self.OPTIONEN[0])
        self.assertEqual((await self._row()).user_response, self.OPTIONEN[0])

    async def test_the_choice_also_rides_along_on_redis(self):
        """Der Agent wartet auf die Veroeffentlichung, nicht nur auf die
        Abfrage — sonst merkt er die Antwort erst beim naechsten Poll."""
        async with self.Session() as db:
            await self._approve(db, self.OPTIONEN[0])
        nutzlast = self._agentenkanal()
        self.assertEqual(nutzlast["status"], "approved")
        # Derselbe Schluessel, den der Telegram-Weg benutzt.
        self.assertEqual(nutzlast["reason"], self.OPTIONEN[0])

    async def test_free_text_works_too(self):
        """Im gemeldeten Fall passte keine Option: eine davon lautete
        „bitte im Chat nennen" — das haette den Nutzer aus dem Fenster
        geschickt."""
        async with self.Session() as db:
            await self._approve(db, "Es heisst Polar, nicht Poros.")
        self.assertEqual((await self._row()).user_response, "Es heisst Polar, nicht Poros.")

    async def test_the_question_counts_as_resolved(self):
        """Sonst fragt der Agent weiter und die Rueckfrage bleibt offen stehen."""
        async with self.Session() as db:
            await self._approve(db, self.OPTIONEN[0])
        zeile = await self._row()
        self.assertEqual(zeile.status, ApprovalStatus.APPROVED)
        self.assertIsNotNone(zeile.resolved_at)


class TheOldBehaviourIsUntouchedTests(AnsweringAQuestionTests):
    """Der bisherige Weg muss unveraendert funktionieren: Telegram, die
    iOS-App und die Reflexions-Freigaben rufen ``approve`` ohne Rumpf auf."""

    async def test_approving_without_an_answer_still_records_who_did_it(self):
        async with self.Session() as db:
            await self._approve(db)
        self.assertEqual((await self._row()).user_response, "Approved by admin@example.test")

    async def test_no_answer_means_no_reason_on_the_channel(self):
        """Ein leerer ``reason`` waere fuer den Agenten eine Antwort, die keine
        ist — er wuerde versuchen, sich danach zu richten."""
        async with self.Session() as db:
            await self._approve(db)
        self.assertNotIn("reason", self._agentenkanal())

    async def test_whitespace_is_not_an_answer(self):
        async with self.Session() as db:
            await self._approve(db, "   ")
        self.assertEqual((await self._row()).user_response, "Approved by admin@example.test")

    async def test_an_already_resolved_request_is_refused(self):
        from fastapi import HTTPException
        async with self.Session() as db:
            await self._approve(db, self.OPTIONEN[0])
        async with self.Session() as db:
            with self.assertRaises(HTTPException):
                await self._approve(db, self.OPTIONEN[1])


class TheUiCanActuallyAnswerTests(unittest.TestCase):
    """Die Schnittstelle allein nuetzt nichts, wenn die Optionen weiter als
    Text dastehen — genau das war der gemeldete Fehler."""

    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[2]
    MODAL = (ROOT / "frontend/src/components/agents/approval-modal.tsx").read_text()
    SEITE = (ROOT / "frontend/src/app/approvals/page.tsx").read_text()
    API = (ROOT / "frontend/src/lib/api.ts").read_text()
    MCP = (ROOT / "agent/mcp/notification-server.mjs").read_text()

    def test_the_modal_renders_options_as_buttons(self):
        block = self.MODAL.split("request.options.map(", 1)[1][:400]
        self.assertIn("<button", block)
        self.assertIn("handleAnswer(opt)", block)

    def test_the_list_renders_them_as_buttons_too(self):
        """Zwei Ansichten derselben Frage, von denen nur eine antworten kann,
        waeren die naechste Beschwerde."""
        block = self.SEITE.split("approval.options.map(", 1)[1][:500]
        self.assertIn("<button", block)
        self.assertIn("handleAnswerInline(", block)

    def test_there_is_a_field_for_an_answer_nobody_offered(self):
        self.assertIn("Oder eigene Antwort", self.MODAL)

    def test_the_answer_is_sent_to_the_server(self):
        block = self.API.split("export async function approveCommand", 1)[1][:400]
        self.assertIn("answer", block)

    def test_the_mcp_runtime_passes_the_answer_to_the_agent(self):
        """Der Custom-LLM-Weg las ``user_response`` schon immer; der MCP-Weg gab
        ihn nur bei Ablehnung weiter — dieselbe Faehigkeit muss in allen
        Laufzeiten vorhanden sein."""
        block = self.MCP.split("const approved = decision.status", 1)[1][:900]
        self.assertIn("user_response", block)
        self.assertIn("APPROVED", block)


if __name__ == "__main__":
    unittest.main()
