"""Abnahme v1.362.1: Der Agent stellte eine beantwortete Rückfrage erneut.

Ablauf auf der Anlage: Rückfrage „Soll ich eine Testnotiz anlegen?“ mit den
Optionen „Weiter“/„Abbrechen“, beantwortet mit „Abbrechen“. Der Agent bekam
darauf (``notification-server.mjs``)::

    User APPROVED the action (approval_id: …). You may proceed.
    Antwort des Nutzers: "Abbrechen" — richte dich danach.

„Genehmigt, mach weiter“ und „Abbrechen“ in einem Satz. Vier Minuten später
stand dieselbe Frage wieder offen. Eine Rückfrage wird nicht genehmigt, sie wird
beantwortet — und das muss beim Agenten genau so ankommen, in JEDER Laufzeit.

Deshalb formuliert der Server die Rückmeldung (``agent_hint`` in
``GET /approvals/check/{id}``), die Laufzeiten reichen sie nur durch.
"""

import unittest

from app.api import approvals as api
from app.models.command_approval import ApprovalStatus, CommandApproval
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


FRAGE = "Soll ich eine Testnotiz anlegen?"


class RueckmeldungAnDenAgenten(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(CommandApproval.metadata.create_all, tables=[CommandApproval.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _pruefen(self, status, antwort, *, frage=FRAGE, befehl="user_decision"):
        async with self.Session() as db:
            zeile = CommandApproval(
                agent_id="a1", command=befehl, description=frage or "rm -rf /tmp/x",
                risk_level="high", status=status, user_response=antwort,
                meta={"question": frage, "options": ["Weiter", "Abbrechen"] if frage else None},
            )
            db.add(zeile)
            await db.commit()
            return await api.check_approval_status(str(zeile.id), agent_auth={"agent_id": "a1"}, db=db)

    async def test_beantwortete_rueckfrage_ist_eine_antwort_keine_genehmigung(self):
        hinweis = (await self._pruefen(ApprovalStatus.APPROVED, "Abbrechen"))["agent_hint"]
        self.assertIn('"Abbrechen"', hinweis)
        self.assertIn(FRAGE, hinweis)
        # Kein „genehmigt, mach weiter“ neben „Abbrechen“.
        self.assertNotIn("APPROVED", hinweis)
        self.assertNotIn("may proceed", hinweis)
        # Und ausdrücklich: nicht noch einmal fragen.
        self.assertIn("do NOT ask", hinweis)

    async def test_bestaetigung_ohne_eigene_antwort_bleibt_eine_freigabe(self):
        hinweis = (await self._pruefen(
            ApprovalStatus.APPROVED, "Approved by admin@example.test",
            frage=None, befehl="bash"))["agent_hint"]
        self.assertIn("APPROVED", hinweis)
        self.assertNotIn("admin@example.test", hinweis)

    async def test_ablehnung_nennt_den_grund_und_haelt_an(self):
        hinweis = (await self._pruefen(ApprovalStatus.DENIED, "Vom Nutzer abgelehnt"))["agent_hint"]
        self.assertIn("DECLINED", hinweis)
        self.assertIn("Vom Nutzer abgelehnt", hinweis)
        self.assertIn("Do NOT", hinweis)
        self.assertIn("do NOT ask", hinweis)

    async def test_verfallen_heisst_nicht_weitermachen(self):
        hinweis = (await self._pruefen(ApprovalStatus.EXPIRED, "Nicht beantwortet"))["agent_hint"]
        self.assertIn("Do NOT", hinweis)

    async def test_offene_frage_hat_noch_keinen_hinweis(self):
        self.assertIsNone((await self._pruefen(ApprovalStatus.PENDING, None))["agent_hint"])

    async def test_bisherige_felder_bleiben(self):
        """Ältere Agenten-Abbilder lesen ``status`` und ``user_response``."""
        stand = await self._pruefen(ApprovalStatus.APPROVED, "Abbrechen")
        self.assertEqual(stand["status"], ApprovalStatus.APPROVED)
        self.assertEqual(stand["user_response"], "Abbrechen")


class LaufzeitenReichenDenHinweisDurch(unittest.TestCase):
    """Harness-Parität: Claude Code/Codex (MCP) und eigenes Modell nehmen denselben Text."""

    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[2]

    def test_mcp_weg(self):
        mcp = (self.ROOT / "agent/mcp/notification-server.mjs").read_text()
        self.assertIn("decision.agent_hint", mcp)

    def test_eigenes_modell(self):
        client = (self.ROOT / "agent/app/tools/api_client.py").read_text()
        self.assertIn('check.get("agent_hint")', client)


if __name__ == "__main__":
    unittest.main()
