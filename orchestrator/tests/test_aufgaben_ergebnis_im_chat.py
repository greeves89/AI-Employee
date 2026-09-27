"""Das Ergebnis einer Aufgabe landet im Chat, aus dem sie kam.

Bis v1.341.0 bekamen nur Delegationen (Agent A beauftragt Agent B) eine Kachel
im Chat. Was sich ein Agent selbst auftrug und jeder Zeitplan-Lauf endeten in
keinem Gespraech — ``publish_task_card`` brach bei ``delegator == agent_id`` ab,
und Zeitplaene kannten ihren Chat gar nicht. Wer im Chat „schick mir jeden
Morgen einen Newsletter" sagte, sah das Ergebnis nie dort.

Geprueft wird die echte Zuordnung (``_faden_fuer_aufgabe``) und die echte
Kachel (``publish_task_card``); ersetzt werden nur Datenbank und Redis.
"""
import asyncio
import json
import unittest
from types import SimpleNamespace

from app.core.task_router import TaskRouter, faden_des_laufenden_zuges, ist_system_zeitplan


class _RedisClient:
    def __init__(self, status=None):
        self.status = status or {}
        self.veroeffentlicht = []

    async def hgetall(self, key):
        return self.status.get(key, {})

    async def publish(self, kanal, nachricht):
        self.veroeffentlicht.append((kanal, json.loads(nachricht)))


class _Db:
    """Genug Datenbank fuer die Zuordnung: Zeitplaene per get, Rest schluckt."""

    def __init__(self, plaene=None):
        self.plaene = plaene or {}
        self.hinzugefuegt = []
        self.commits = 0

    async def get(self, model, key):
        return self.plaene.get(key)

    async def execute(self, stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    def add(self, obj):
        self.hinzugefuegt.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


def _router(db, redis_client):
    r = TaskRouter.__new__(TaskRouter)
    r.db = db
    r.redis = SimpleNamespace(client=redis_client)
    return r


def _aufgabe(agent_id="a1", **meta):
    return SimpleNamespace(
        id="t1", title="[Scheduled] Newsletter", agent_id=agent_id, metadata_=meta,
        status=SimpleNamespace(value="completed"), result="Inhalt", error=None,
        cost_usd=None, duration_ms=1000,
    )


def lauf(coro):
    return asyncio.run(coro)


class ZuordnungTests(unittest.TestCase):
    def test_selbst_auftrag_landet_im_laufenden_chat(self):
        redis = _RedisClient({"agent:a1:status": {"current_task": "chat:s-42"}})
        besitzer, faden = lauf(_router(_Db(), redis)._faden_fuer_aufgabe(_aufgabe(created_by_agent="a1")))
        self.assertEqual((besitzer, faden), ("a1", "s-42"))

    def test_zeitplan_mit_herkunft(self):
        plan = SimpleNamespace(id="p1", name="Newsletter", agent_id="a1", chat_session_id="s-7")
        besitzer, faden = lauf(_router(_Db({"p1": plan}), _RedisClient())._faden_fuer_aufgabe(_aufgabe(schedule_id="p1")))
        self.assertEqual((besitzer, faden), ("a1", "s-7"))

    def test_zeitplan_ohne_herkunft_bekommt_eigenes_gespraech(self):
        plan = SimpleNamespace(id="p2", name="Wochenbericht", agent_id="a1", chat_session_id=None)
        db = _Db({"p2": plan})
        besitzer, faden = lauf(_router(db, _RedisClient())._faden_fuer_aufgabe(_aufgabe(schedule_id="p2")))
        self.assertEqual(faden, "zeitplan-p2")
        self.assertEqual(plan.chat_session_id, "zeitplan-p2")  # gemerkt fuer den naechsten Lauf
        benannt = [o for o in db.hinzugefuegt if getattr(o, "session_id", None) == "zeitplan-p2"]
        self.assertEqual([o.title for o in benannt], ["Wochenbericht"])

    def test_system_zeitplaene_bekommen_keine_kachel(self):
        for name in ("[Proactive] Ben", "[Rhythmus] Abendplanung"):
            plan = SimpleNamespace(id="p3", name=name, agent_id="a1", chat_session_id=None)
            _, faden = lauf(_router(_Db({"p3": plan}), _RedisClient())._faden_fuer_aufgabe(_aufgabe(schedule_id="p3")))
            self.assertIsNone(faden, name)
        self.assertFalse(ist_system_zeitplan("Newsletter"))

    def test_formular_aufgabe_ohne_chat(self):
        redis = _RedisClient({"agent:a1:status": {"current_task": "chat:s-42"}})
        _, faden = lauf(_router(_Db(), redis)._faden_fuer_aufgabe(_aufgabe()))
        self.assertIsNone(faden)  # nicht in einen fremden, gerade offenen Chat

    def test_gemerkter_faden_gilt(self):
        _, faden = lauf(_router(_Db(), _RedisClient())._faden_fuer_aufgabe(
            _aufgabe(created_by_agent="lead", chat_session_id="s-1")))
        self.assertEqual(faden, "s-1")


class KachelTests(unittest.TestCase):
    def test_selbst_auftrag_bekommt_kachel_mit_ergebnis(self):
        """Der alte Abbruch: ``delegator == agent_id`` → keine Kachel."""
        redis = _RedisClient({"agent:a1:status": {"current_task": "chat:s-42"}})
        router = _router(_Db(), redis)
        lauf(router.publish_task_card(_aufgabe(created_by_agent="a1"), "done"))
        self.assertEqual(len(redis.veroeffentlicht), 1)
        kanal, nachricht = redis.veroeffentlicht[0]
        self.assertEqual(kanal, "agent:a1:chat:response")
        self.assertEqual(nachricht["session_id"], "s-42")
        self.assertEqual(nachricht["data"]["result_preview"], "Inhalt")


class LaufenderZugTests(unittest.TestCase):
    def test_mehrere_offene_gespraeche_sind_nicht_eindeutig(self):
        redis = SimpleNamespace(client=_RedisClient({"agent:a1:status": {
            "current_task": "task:x", "active_sessions": json.dumps(["chat:s1", "chat:s2"])}}))
        self.assertIsNone(lauf(faden_des_laufenden_zuges(redis, "a1")))

    def test_ein_offenes_gespraech_neben_einer_aufgabe(self):
        redis = SimpleNamespace(client=_RedisClient({"agent:a1:status": {
            "current_task": "task:x", "active_sessions": json.dumps(["task:x", "chat:s1"])}}))
        self.assertEqual(lauf(faden_des_laufenden_zuges(redis, "a1")), "s1")


if __name__ == "__main__":
    unittest.main()
