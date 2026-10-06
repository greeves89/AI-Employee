"""Der Agent antwortet einem Kollegen über den Orchestrator (#918).

Bis v1.362.6 schrieb er selbst in ``agent:{to}:messages`` — am Orchestrator
vorbei (kein Budget, keine Besitzprüfung, Absender frei wählbar).
"""

import asyncio
import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from app import message_consumer as mc


class _Antwort(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class AntwortUeberDieApi(unittest.TestCase):
    def setUp(self):
        self.consumer = mc.MessageConsumer.__new__(mc.MessageConsumer)
        self.consumer.agent_id = "ich"
        self.consumer.redis = MagicMock()

    def _senden(self, **urlopen):
        with patch("urllib.request.urlopen", **urlopen) as offen:
            ok = asyncio.run(self.consumer._send_reply("kollege", "Antworttext", reply_to="m1"))
        return ok, offen

    def test_antwort_geht_an_die_api_mit_typ_und_bezug(self):
        ok, offen = self._senden(return_value=_Antwort(b"{}"))
        self.assertTrue(ok)
        req = offen.call_args.args[0]
        self.assertTrue(req.full_url.endswith("/api/v1/agents/kollege/message"))
        body = json.loads(req.data)
        self.assertEqual(body["message_type"], "response")
        self.assertEqual(body["reply_to"], "m1")
        self.assertEqual(body["text"], "Antworttext")
        self.assertNotIn("from_agent_id", body, "den Absender setzt der Server aus dem Token")

    def test_kein_direkter_schreibzugriff_auf_redis(self):
        self._senden(return_value=_Antwort(b"{}"))
        self.consumer.redis.lpush.assert_not_called()
        self.consumer.redis.publish.assert_not_called()

    def test_absage_des_orchestrators_ist_kein_absturz(self):
        for code in (402, 403, 429):
            fehler = urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b""))
            ok, _ = self._senden(side_effect=fehler)
            self.assertFalse(ok, code)

    def test_antwort_wird_am_typ_erkannt(self):
        """Ein Empfänger darf auf eine Antwort nicht seinerseits antworten (Ping-Pong)."""
        import inspect

        quelle = inspect.getsource(mc.MessageConsumer.start)
        self.assertIn('msg.get("message_type") == "response"', quelle)


if __name__ == "__main__":
    unittest.main()


class NachrichtenLaeufeMeldenIhreKosten(unittest.TestCase):
    """#919: Läufe auf eine Kollegen-Nachricht legten weder Aufgabe noch
    Chatzeile an — ihre Kosten fehlten in Budget und Dashboard."""

    def _consumer(self):
        from unittest.mock import AsyncMock

        c = mc.MessageConsumer.__new__(mc.MessageConsumer)
        c.agent_id = "ich"
        c.redis = MagicMock()
        c.redis.rpush = AsyncMock()
        c.redis.ltrim = AsyncMock()
        return c

    def test_kosten_gehen_in_die_eigene_fertig_liste(self):
        c = self._consumer()

        async def lauf(prompt, model=None):
            c._lauf_kosten += 0.0421
            return "Antwort"

        c._execute_cli_roh = lauf
        self.assertEqual(asyncio.run(c._execute_cli("Frage")), "Antwort")
        schluessel, roh = c.redis.rpush.call_args.args
        self.assertEqual(schluessel, "agent:ich:chat:done", "nur der eigene Namensraum")
        ereignis = json.loads(roh)
        self.assertEqual(ereignis["type"], "done")
        self.assertAlmostEqual(ereignis["data"]["cost_usd"], 0.0421)
        self.assertTrue(ereignis["message_id"].startswith("nachricht-"))

    def test_kostenloser_lauf_meldet_nichts(self):
        c = self._consumer()

        async def lauf(prompt, model=None):
            return "Antwort"

        c._execute_cli_roh = lauf
        asyncio.run(c._execute_cli("Frage"))
        c.redis.rpush.assert_not_called()

    def test_auch_ein_gescheiterter_lauf_meldet_was_er_gekostet_hat(self):
        c = self._consumer()

        async def lauf(prompt, model=None):
            c._lauf_kosten += 0.01
            raise RuntimeError("abgebrochen")

        c._execute_cli_roh = lauf
        with self.assertRaises(RuntimeError):
            asyncio.run(c._execute_cli("Frage"))
        c.redis.rpush.assert_called_once()

    def test_jeder_lauf_zaehlt_fuer_sich(self):
        c = self._consumer()

        async def lauf(prompt, model=None):
            c._lauf_kosten += 0.02
            return "x"

        c._execute_cli_roh = lauf
        asyncio.run(c._execute_cli("a"))
        asyncio.run(c._execute_cli("b"))
        betraege = [json.loads(a.args[1])["data"]["cost_usd"] for a in c.redis.rpush.call_args_list]
        self.assertEqual(betraege, [0.02, 0.02])
