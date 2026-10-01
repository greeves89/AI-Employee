"""Delegierte Auftraege tragen ihren Eltern-Auftrag (#880).

Kein Werkzeug hat ``parent_task_id`` je gesetzt: bei Claude Code fehlte der
Parameter, bei Codex und Custom-LLM wurde er im Werkzeug angeboten und in
``_task_payload`` verworfen. Der Server leitet den Eltern-Auftrag jetzt aus dem
Status des Agenten ab — eine Stelle fuer alle Laufzeiten — und prueft eine
ausdrueckliche Angabe gegen den Besitzer.
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

from app.core.task_router import TaskRouter, auftrag_des_laufenden_zuges
from app.models.agent import Agent
from app.models.task import Task


def _redis(status: dict | None):
    client = MagicMock()
    client.hgetall = AsyncMock(return_value={k.encode(): v.encode() for k, v in (status or {}).items()})
    return SimpleNamespace(client=client)


def _status(*laufend, current=""):
    return {"current_task": current, "active_sessions": json.dumps(list(laufend))}


class LaufenderAuftrag(unittest.IsolatedAsyncioTestCase):
    async def _auftrag(self, status):
        return await auftrag_des_laufenden_zuges(_redis(status), "agent1")

    async def test_genau_ein_auftrag(self):
        self.assertEqual(await self._auftrag(_status("t-1", current="t-1")), "t-1")

    async def test_auftrag_neben_einem_gespraech(self):
        """Das Gespraech ist kein Auftrag — der eine Auftrag bleibt eindeutig."""
        self.assertEqual(await self._auftrag(_status("chat:abc", "t-1", current="chat:abc")), "t-1")

    async def test_zwei_auftraege_sind_nicht_eindeutig(self):
        self.assertIsNone(await self._auftrag(_status("t-1", "t-2", current="t-1")))

    async def test_nur_gespraech_oder_nachricht(self):
        self.assertIsNone(await self._auftrag(_status("chat:abc", current="chat:abc")))
        self.assertIsNone(await self._auftrag(_status("msg:9", current="msg:9")))

    async def test_kein_status(self):
        self.assertIsNone(await self._auftrag(None))
        self.assertIsNone(await auftrag_des_laufenden_zuges(_redis({}), None))

    async def test_kaputter_status_bricht_nicht(self):
        self.assertIsNone(await self._auftrag({"current_task": "", "active_sessions": "{kaputt"}))


def _router(status, objekte: dict):
    db = MagicMock()
    db.get = AsyncMock(side_effect=lambda modell, pk: objekte.get((modell, pk)))
    return TaskRouter(db, _redis(status), MagicMock())


AGENTEN = {
    (Agent, "a1"): Agent(id="a1", name="A1", user_id="u1"),
    (Agent, "a2"): Agent(id="a2", name="A2", user_id="u1"),
    (Agent, "b1"): Agent(id="b1", name="B1", user_id="u2"),
}


class ElternAuftrag(unittest.IsolatedAsyncioTestCase):
    async def test_mensch_legt_an_angabe_gilt_wie_bisher(self):
        router = _router(None, {})
        self.assertEqual(await router._eltern_auftrag("t-9", None), "t-9")
        self.assertIsNone(await router._eltern_auftrag(None, None))

    async def test_agent_ohne_angabe_bekommt_seinen_laufenden_auftrag(self):
        router = _router(_status("t-1", current="t-1"),
                         {**AGENTEN, (Task, "t-1"): Task(id="t-1", agent_id="a1", title="x", prompt="x")})
        self.assertEqual(await router._eltern_auftrag(None, "a1"), "t-1")

    async def test_status_nennt_einen_auftrag_den_es_nicht_gibt(self):
        """Der Status kommt aus dem Container — nachsehen statt glauben."""
        router = _router(_status("t-fantasie", current="t-fantasie"), dict(AGENTEN))
        self.assertIsNone(await router._eltern_auftrag(None, "a1"))

    async def test_status_nennt_den_auftrag_eines_anderen_agenten(self):
        router = _router(_status("t-b", current="t-b"),
                         {**AGENTEN, (Task, "t-b"): Task(id="t-b", agent_id="b1", title="x", prompt="x")})
        self.assertIsNone(await router._eltern_auftrag(None, "a1"))

    async def test_agent_im_gespraech_hat_keinen_eltern_auftrag(self):
        router = _router(_status("chat:abc", current="chat:abc"), dict(AGENTEN))
        self.assertIsNone(await router._eltern_auftrag(None, "a1"))

    async def test_angabe_auf_auftrag_eines_kollegen_desselben_nutzers(self):
        router = _router(None, {**AGENTEN, (Task, "t-2"): Task(id="t-2", agent_id="a2", title="x", prompt="x")})
        self.assertEqual(await router._eltern_auftrag("t-2", "a1"), "t-2")

    async def test_angabe_auf_fremden_auftrag_wird_abgewiesen(self):
        """User B's Auftrag ist fuer einen Agenten von User A tabu."""
        router = _router(None, {**AGENTEN, (Task, "t-b"): Task(id="t-b", agent_id="b1", title="x", prompt="x")})
        with self.assertRaises(HTTPException) as fehler:
            await router._eltern_auftrag("t-b", "a1")
        self.assertEqual(fehler.exception.status_code, 403)

    async def test_angabe_auf_unbekannten_auftrag(self):
        router = _router(None, dict(AGENTEN))
        with self.assertRaises(HTTPException) as fehler:
            await router._eltern_auftrag("gibt-es-nicht", "a1")
        self.assertEqual(fehler.exception.status_code, 404)

    async def test_agent_ohne_besitzer_darf_sich_nirgends_anhaengen(self):
        objekte = {(Agent, "x"): Agent(id="x", name="X", user_id=None),
                   (Agent, "y"): Agent(id="y", name="Y", user_id=None),
                   (Task, "t-y"): Task(id="t-y", agent_id="y", title="x", prompt="x")}
        with self.assertRaises(HTTPException):
            await _router(None, objekte)._eltern_auftrag("t-y", "x")


class KeineDoppelteRueckmeldung(unittest.IsolatedAsyncioTestCase):
    """Der Auftraggeber bekommt die Delegations-Rueckmeldung. Gehoert ihm auch
    der Eltern-Auftrag, waere die Eltern-Rueckmeldung eine zweite fuer dasselbe."""

    async def test_eltern_gehoert_dem_auftraggeber(self):
        router = _router(None, {(Task, "t-1"): Task(id="t-1", agent_id="a1", title="x", prompt="x")})
        kind = Task(id="t-2", agent_id="a2", parent_task_id="t-1", title="x", prompt="x")
        self.assertTrue(await router._eltern_ist_auftraggeber(kind, "a1"))

    async def test_eltern_gehoert_jemand_anderem(self):
        router = _router(None, {(Task, "t-1"): Task(id="t-1", agent_id="a2", title="x", prompt="x")})
        kind = Task(id="t-2", agent_id="a3", parent_task_id="t-1", title="x", prompt="x")
        self.assertFalse(await router._eltern_ist_auftraggeber(kind, "a1"))

    async def test_ohne_auftraggeber_oder_ohne_eltern(self):
        router = _router(None, {})
        self.assertFalse(await router._eltern_ist_auftraggeber(Task(id="t", parent_task_id="p", title="x", prompt="x"), None))
        self.assertFalse(await router._eltern_ist_auftraggeber(Task(id="t", title="x", prompt="x"), "a1"))


if __name__ == "__main__":
    unittest.main()
