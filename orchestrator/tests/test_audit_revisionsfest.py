"""Prüfprotokoll revisionsfest (#908).

Befund aus dem Markttest: eine mit „Abbrechen“ beantwortete Rückfrage stand als
„command approved · user_decision {} · success“ im Protokoll — ohne Person, mit
Agentenkennung statt Namen. Verwerfen, „alle verwerfen“ und Ablauf hinterließen
gar keinen Eintrag, Anmeldungen und Verwaltungsschritte auch nicht.

Geprüft wird hier das VERHALTEN: welche Zeilen nach einer Handlung im Protokoll
stehen, wer sie sieht und was der Export liefert.
"""

import csv
import io
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.api import approvals as api
from app.api import audit as audit_api
from app.core.audit import protokolliere
from app.models.agent import Agent, AgentState
from app.models.agent_access import AgentAccess
from app.models.audit_log import AuditEventType, AuditLog
from app.models.command_approval import ApprovalStatus, CommandApproval
from app.models.custom_role import CustomRole
from app.models.notification import Notification
from app.models.user import User, UserRole


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _admin():
    return SimpleNamespace(id="u1", role=UserRole.ADMIN, email="chefin@example.test", name="Chefin")


def _member(uid="u2"):
    return SimpleNamespace(id=uid, role=UserRole.MEMBER, email=f"{uid}@example.test", name="Mitglied")


class _Basis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (CustomRole, User, Agent, AgentAccess, CommandApproval, Notification, AuditLog):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(User(id="u1", email="chefin@example.test", name="Chefin", role=UserRole.ADMIN))
            db.add(User(id="u2", email="u2@example.test", name="Mitglied", role=UserRole.MEMBER))
            db.add(Agent(id="a1", name="Buchhaltung", state=AgentState.RUNNING, user_id="u2", config={}))
            db.add(Agent(id="a2", name="Fremd", state=AgentState.RUNNING, user_id="u9", config={}))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _frage(self, db, agent_id="a1", optionen=("Weiter", "Abbrechen"), question="Soll ich weitermachen?"):
        res = await api.request_approval(
            api.ApprovalRequest(question=question, options=list(optionen)),
            agent_auth={"agent_id": agent_id}, db=db,
        )
        return res["approval_id"]

    async def _befehl(self, db, agent_id="a1", tool="bash", cmd="rm -rf /tmp/x"):
        res = await api.request_approval(
            api.ApprovalRequest(tool=tool, input={"command": cmd}, reasoning="aufraeumen"),
            agent_auth={"agent_id": agent_id}, db=db,
        )
        return res["approval_id"]

    async def _eintraege(self, db, typ=None):
        q = select(AuditLog).order_by(AuditLog.id)
        if typ:
            q = q.where(AuditLog.event_type == typ)
        return (await db.execute(q)).scalars().all()

    async def _liste(self, db, user, **kw):
        params = dict(agent_id=None, task_id=None, event_type=None, outcome=None,
                      since=None, until=None, limit=100, offset=0)
        params.update(kw)
        return await audit_api.list_audit_logs(**params, user=user, db=db)


class RueckfragenSindKeineFreigaben(_Basis):
    async def test_abbrechen_wird_als_antwort_protokolliert_nicht_als_freigabe(self):
        async with self.Session() as db:
            aid = await self._frage(db)
            await api.approve_request(aid, api.ApprovalAnswer(answer="Abbrechen"), user=_admin(), db=db)

            self.assertEqual(await self._eintraege(db, "command_approved"), [],
                             "Eine Rückfrage-Antwort ist keine Freigabe")
            [eintrag] = await self._eintraege(db, "question_answered")
            self.assertEqual(eintrag.command, "Rückfrage beantwortet: Abbrechen")
            self.assertEqual(eintrag.meta["answer"], "Abbrechen")
            self.assertEqual(eintrag.meta["option"], "Abbrechen")
            self.assertEqual(eintrag.user_id, "u1")
            self.assertEqual(eintrag.meta["agent_name"], "Buchhaltung")

            zeile = await db.get(CommandApproval, int(aid))
            self.assertEqual(zeile.resolved_by_user_id, "u1")

    async def test_ein_befehl_bleibt_eine_freigabe(self):
        async with self.Session() as db:
            aid = await self._befehl(db)
            await api.approve_request(aid, None, user=_admin(), db=db)
            [eintrag] = await self._eintraege(db, "command_approved")
            self.assertEqual(eintrag.user_id, "u1")
            self.assertEqual(await self._eintraege(db, "question_answered"), [])

    async def test_freitext_antwort_hat_keine_option(self):
        async with self.Session() as db:
            aid = await self._frage(db)
            await api.approve_request(aid, api.ApprovalAnswer(answer="Erst morgen"), user=_admin(), db=db)
            [eintrag] = await self._eintraege(db, "question_answered")
            self.assertEqual(eintrag.meta["answer"], "Erst morgen")
            self.assertIsNone(eintrag.meta["option"])


class VerwerfenUndAblaufWerdenProtokolliert(_Basis):
    async def test_einzeln_verwerfen(self):
        async with self.Session() as db:
            aid = await self._befehl(db)
            await api.cancel_approval_request(aid, user=_admin(), db=db)
            [eintrag] = await self._eintraege(db, "approval_cancelled")
            self.assertEqual(eintrag.approval_id, aid)
            self.assertEqual(eintrag.user_id, "u1")

    async def test_verwerfen_einer_erledigten_freigabe_wird_abgewiesen(self):
        """Sonst überschriebe ein spätes Verwerfen Entscheider und Ergebnis."""
        async with self.Session() as db:
            aid = await self._befehl(db)
            await api.approve_request(aid, None, user=_admin(), db=db)
            with self.assertRaises(HTTPException) as ctx:
                await api.cancel_approval_request(aid, user=_admin(), db=db)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(await self._eintraege(db, "approval_cancelled"), [])

    async def test_alle_verwerfen_schreibt_je_freigabe_einen_eintrag(self):
        async with self.Session() as db:
            ids = [await self._frage(db, question=f"Frage {i}?") for i in range(3)]
            res = await api.clear_pending_approvals(user=_admin(), db=db)
            self.assertEqual(res["cleared"], 3)
            eintraege = await self._eintraege(db, "approval_cancelled")
            self.assertEqual(sorted(e.approval_id for e in eintraege), sorted(ids))
            self.assertTrue(all(e.meta.get("sammelverwerfung") for e in eintraege))
            self.assertTrue(all(e.user_id == "u1" for e in eintraege))

    async def test_ablauf(self):
        from app.services.scheduler_service import _APPROVAL_TTL_HOURS, SchedulerService

        async with self.Session() as db:
            db.add(CommandApproval(
                agent_id="a1", command="user_decision", description="alt",
                status=ApprovalStatus.PENDING, meta={"question": "Noch da?"},
                created_at=datetime.now(timezone.utc) - timedelta(hours=_APPROVAL_TTL_HOURS + 1),
            ))
            await db.commit()

        with patch("app.db.session.resilient_session", self.Session):
            n = await SchedulerService._expire_stale_approvals(SimpleNamespace())
        self.assertEqual(n, 1)
        async with self.Session() as db:
            [eintrag] = await self._eintraege(db, "approval_expired")
            self.assertIsNone(eintrag.user_id)
            self.assertEqual(eintrag.outcome, "expired")


class AnmeldungWirdProtokolliert(_Basis):
    async def test_fehllogin_ohne_passwort(self):
        from app.api import auth as auth_api

        geheim = "Falsch-Passwort-123"
        async with self.Session() as db:
            with self.assertRaises(HTTPException):
                await auth_api.login(
                    auth_api.LoginRequest(email="u2@example.com", password=geheim),
                    SimpleNamespace(), SimpleNamespace(set_cookie=lambda *a, **k: None), db,
                )
            [eintrag] = await self._eintraege(db, "login_failed")
            self.assertNotIn(geheim, repr(eintrag.command) + repr(eintrag.meta))
            self.assertEqual(eintrag.agent_id, "auth")
            self.assertEqual(eintrag.meta.get("email"), "u2@example.com")
        auth_api._clear_login_attempts("u2@example.com")

    async def test_mitglied_sieht_keine_anmeldeereignisse(self):
        async with self.Session() as db:
            await protokolliere(db, AuditEventType.LOGIN_SUCCEEDED, agent_id="auth", user_id="u2")
            await protokolliere(db, AuditEventType.COMMAND_EXECUTED, agent_id="a1", command="ls")
            await db.commit()

            res = await self._liste(db, _member())
            typen = {e["event_type"] for e in res["logs"]}
            self.assertEqual(typen, {"command_executed"})

            admin = await self._liste(db, _admin())
            self.assertIn("login_succeeded", {e["event_type"] for e in admin["logs"]})


class NamenUndPerson(_Basis):
    async def test_namen_fuer_fremde_und_geloeschte_agenten(self):
        async with self.Session() as db:
            db.add(Agent(id="a3", name="Spaeter-geloescht", state=AgentState.RUNNING, user_id="u9", config={}))
            await db.commit()
            await protokolliere(db, AuditEventType.COMMAND_EXECUTED, agent_id="a2", command="ls")
            await protokolliere(db, AuditEventType.COMMAND_EXECUTED, agent_id="a3", command="ls")
            await db.commit()
            await db.execute(delete(Agent).where(Agent.id == "a3"))
            await db.commit()

            res = await self._liste(db, _admin())
            namen = {e["agent_id"]: e["agent_name"] for e in res["logs"]}
            self.assertEqual(namen["a2"], "Fremd")
            self.assertEqual(namen["a3"], "Spaeter-geloescht")

    async def test_person_wird_aufgeloest(self):
        async with self.Session() as db:
            aid = await self._frage(db)
            await api.approve_request(aid, api.ApprovalAnswer(answer="Weiter"), user=_admin(), db=db)
            res = await self._liste(db, _admin(), event_type="question_answered")
            [eintrag] = res["logs"]
            self.assertEqual(eintrag["user_id"], "u1")
            self.assertEqual(eintrag["person"], "Chefin")


class Export(_Basis):
    async def _export(self, db, user, **kw):
        params = dict(agent_id=None, task_id=None, event_type=None, outcome=None, since=None, until=None)
        params.update(kw)
        return await audit_api.export_audit_logs(**params, user=user, db=db)

    async def test_export_filtert_und_hat_bom(self):
        async with self.Session() as db:
            await protokolliere(db, AuditEventType.LOGIN_SUCCEEDED, agent_id="auth", user_id="u1",
                                command="Anmeldung über Passwort")
            await protokolliere(db, AuditEventType.COMMAND_EXECUTED, agent_id="a1", command="grüße")
            await db.commit()

            antwort = await self._export(db, _admin(), event_type="command_executed")
            roh = antwort.body.decode("utf-8")
            self.assertTrue(roh.startswith("﻿"), "Excel braucht die BOM für Umlaute")
            zeilen = list(csv.reader(io.StringIO(roh.lstrip("﻿")), delimiter=";"))
            daten = zeilen[1:]
            self.assertEqual(len(daten), 1)
            self.assertIn("grüße", daten[0])
            self.assertIn("Buchhaltung", daten[0])
            # Der Export selbst steht im Protokoll.
            self.assertEqual(len(await self._eintraege(db, "audit_exported")), 1)

    async def test_formeln_werden_entschaerft(self):
        async with self.Session() as db:
            await protokolliere(db, AuditEventType.COMMAND_EXECUTED, agent_id="a1",
                                command='=HYPERLINK("https://example.invalid","x")')
            await db.commit()
            roh = (await self._export(db, _admin(), event_type="command_executed")).body.decode("utf-8")
            daten = list(csv.reader(io.StringIO(roh.lstrip("\ufeff")), delimiter=";"))[1:]
            befehl = daten[0][[t for _, t in audit_api._EXPORT_SPALTEN].index("Befehl / Vorgang")]
            self.assertTrue(befehl.startswith("'="), befehl)

    def test_harmlose_zellen_bleiben_unveraendert(self):
        for wert, erwartet in [(None, ""), ("grüße", "grüße"), (3, "3"), ("-1", "'-1"), ("@SUM(A1)", "'@SUM(A1)")]:
            self.assertEqual(audit_api._csv_zelle(wert), erwartet)

    async def test_mitglied_darf_nicht_exportieren(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await self._export(db, _member())
            self.assertEqual(ctx.exception.status_code, 403)


class McpAenderungOhneZugangsdaten(_Basis):
    async def test_url_nur_mit_schema_und_host(self):
        from app.api.mcp_servers import _mcp_aenderung_protokollieren

        async with self.Session() as db:
            await _mcp_aenderung_protokollieren(
                db, _admin(), "angelegt", "Beispiel",
                url="https://nutzer:geheim@mcp.example.invalid:8443/t/abc123/mcp?token=xyz",
            )
            await db.commit()
            eintrag = (await self._eintraege(db, "mcp_server_changed"))[0]
        self.assertEqual(eintrag.meta["url"], "https://mcp.example.invalid:8443")
        for geheim in ("geheim", "abc123", "xyz", "nutzer"):
            self.assertNotIn(geheim, str(eintrag.meta))


class AgentDarfMenschlicheEreignisseNichtFaelschen(_Basis):
    async def test_agent_kann_keine_freigabe_eintragen(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await audit_api.create_audit_log(
                    audit_api.AuditLogCreate(event_type="question_answered"),
                    agent_auth={"agent_id": "a1"}, db=db,
                )
            self.assertEqual(ctx.exception.status_code, 400)

    # Sicherheitsprüfung v1.362.0, F9: statt einer Sperrliste eine Erlaubt-Liste.
    # Ein Agent meldet nur, was ER getan hat oder was ihm verweigert wurde —
    # nicht „Agent angelegt“, „Regel geändert“ oder Ähnliches, das sonst jedes neue
    # Ereignis automatisch fälschbar machte. Der Agenten-Code selbst ruft
    # POST /audit/log derzeit nirgends auf (agent/, Brücke, Skills durchsucht).
    async def test_nur_eigene_handlungen_des_agenten(self):
        erlaubt = ("command_executed", "command_blocked", "file_written",
                   "network_request", "url_blocked", "logs_read")
        async with self.Session() as db:
            for typ in erlaubt:
                antwort = await audit_api.create_audit_log(
                    audit_api.AuditLogCreate(event_type=typ, command="ls"),
                    agent_auth={"agent_id": "a1"}, db=db,
                )
                self.assertEqual(antwort["status"], "logged", typ)
            for typ in ("agent_created", "approval_rule_updated", "autonomy_level_changed",
                        "brain_deleted", "dlp_blocked", "mcp_tool_called"):
                with self.assertRaises(HTTPException) as ctx:
                    await audit_api.create_audit_log(
                        audit_api.AuditLogCreate(event_type=typ),
                        agent_auth={"agent_id": "a1"}, db=db,
                    )
                self.assertEqual(ctx.exception.status_code, 400, typ)
            self.assertEqual(len(await self._eintraege(db, "agent_created")), 0)

    def test_laenge_begrenzt(self):
        from pydantic import ValidationError

        audit_api.AuditLogCreate(event_type="command_executed", command="x" * 4000,
                                 meta={"ausgabe": "y" * 15000})
        with self.assertRaises(ValidationError):
            audit_api.AuditLogCreate(event_type="command_executed", command="x" * 4001)
        with self.assertRaises(ValidationError):
            audit_api.AuditLogCreate(event_type="command_executed", meta={"ausgabe": "y" * 17000})


if __name__ == "__main__":
    unittest.main()
