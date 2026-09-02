"""Wer in einem Chat redet, muss in ihm auch reagieren duerfen.

`/react` holte den Bot-Token starr ueber die eigene Agenten-Kennung. Ein Agent
ohne eigenen Bot bekam damit 400 — obwohl er im Chat steht: `/agent <Name>`
schaltet die Weiche auf ihn, und seine Antworten gehen seither ueber den
Gateway-Bot des Kollegen raus. Er durfte also antworten, aber nicht reagieren.

Die Gegenrichtung ist genauso wichtig: Ohne Bedingung koennte sich jeder Agent
mit einer fremden chat_id den Bot eines beliebigen anderen ausleihen.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

from app.api import telegram_actions as ta


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class _FakeDb:
    """Liefert der Reihe nach die vorbereiteten Abfrageergebnisse."""

    def __init__(self, *ergebnisse):
        self._ergebnisse = list(ergebnisse)

    async def execute(self, _query):
        return _FakeResult(self._ergebnisse.pop(0))


class _FakeRedis:
    def __init__(self, aktiver_agent=None, auth=()):
        self._aktiv = aktiver_agent
        self._auth = set(auth)

    async def get(self, _key):
        return self._aktiv

    async def sismember(self, key, chat_id):
        return (key.split(":")[1], chat_id) in self._auth

    async def aclose(self):
        return None


def _agent(agent_id, token=None):
    return SimpleNamespace(id=agent_id, config={"telegram_bot_token": token} if token else {})


def _run(agent_id, chat_id, db, redis):
    with mock.patch.object(ta.aioredis, "from_url", return_value=redis):
        return asyncio.run(ta._bot_token_for_chat(agent_id, chat_id, db))


class BotTokenForChatTests(unittest.TestCase):
    def test_eigener_bot_gewinnt(self):
        """Wer einen eigenen Bot hat, benutzt immer ihn — kein Redis noetig."""
        db = _FakeDb([_agent("a1", "TOKEN-A1")])
        token = _run("a1", "111222333", db, _FakeRedis())
        self.assertEqual(token, "TOKEN-A1")

    def test_ohne_eigenen_bot_zaehlt_der_gateway_des_chats(self):
        """Der Fall `/agent <Name>`: fremder Bot, aber die Weiche zeigt auf mich."""
        db = _FakeDb([_agent("a2")], [_agent("gw", "TOKEN-GW"), _agent("x")])
        redis = _FakeRedis(aktiver_agent="a2", auth={("gw", "111222333")})
        self.assertEqual(_run("a2", "111222333", db, redis), "TOKEN-GW")

    def test_fremder_bot_bleibt_zu_wenn_die_weiche_nicht_auf_mich_zeigt(self):
        """Sonst leiht sich jeder Agent mit geratener chat_id einen fremden Bot."""
        db = _FakeDb([_agent("a3")])
        redis = _FakeRedis(aktiver_agent="jemand-anderes", auth={("gw", "111222333")})
        with self.assertRaises(HTTPException) as fall:
            _run("a3", "111222333", db, redis)
        self.assertEqual(fall.exception.status_code, 400)

    def test_kein_bot_im_ganzen_chat(self):
        """Weiche zeigt auf mich, aber kein Bot kennt den Chat — klare Absage."""
        db = _FakeDb([_agent("a4")], [_agent("gw", "TOKEN-GW")])
        redis = _FakeRedis(aktiver_agent="a4", auth=set())
        with self.assertRaises(HTTPException) as fall:
            _run("a4", "111222333", db, redis)
        self.assertEqual(fall.exception.status_code, 400)

    def test_unbekannter_agent(self):
        db = _FakeDb([])
        with self.assertRaises(HTTPException) as fall:
            _run("gibtsnicht", "111222333", db, _FakeRedis())
        self.assertEqual(fall.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
