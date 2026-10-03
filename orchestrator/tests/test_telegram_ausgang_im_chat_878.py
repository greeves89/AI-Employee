"""#878: Was eine Aufgabe in einen Telegram-Chat schickt, kennt der nächste Chat-Zug dort.

Gemeldeter Fall: Aufgabe schickt „Artikel fertig: …" mit Freigabe-Knöpfen,
der Nutzer antwortet im selben Chat „Pack in den Titel …" — der Agent wusste
nicht, welcher Artikel gemeint ist, weil Aufgabe (Sitzung ``scheduler``) und
Gespräch (``telegram:<chat>``) getrennt laufen.

Geprüft wird:
  1. ausgang merkt sich je Bot und Chat die letzten Sendungen (gekürzt, höchstens 5)
     und gibt sie GENAU EINMAL als Vorspann zurück.
  2. Ein Redis-Fehler stört weder Senden noch Empfangen.
  3. Die Werkzeug-API (_tg_request) merkt Sendungen, Entwürfe nicht.
  4. Push (send_telegram), Freigaben (send_to_all_authorized) und beide
     Eingangswege (Text, Medien) sind angeschlossen.
  5. Gegen echtes Redis (ohne REDIS_URL übersprungen).
"""

import asyncio
import os
import pathlib
import unittest
from unittest import mock

from app.telegram import ausgang

_BOT_SRC = pathlib.Path(__file__).resolve().parents[1] / "app/telegram/agent_bot.py"
_REDIS_URL = os.environ.get("REDIS_URL", "")


class _FakePipe:
    def __init__(self, r):
        self._r, self._calls = r, []

    def __getattr__(self, name):
        def queue(*a, **k):
            self._calls.append((name, a, k))
            return self
        return queue

    async def execute(self):
        out = []
        for name, a, k in self._calls:
            out.append(await getattr(self._r, name)(*a, **k))
        self._calls = []
        return out

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeRedis:
    """Listen wie Redis: rpush, ltrim (negative Indizes), lrange, delete, expire."""

    def __init__(self, store, fail=False):
        self._s, self._fail = store, fail

    def pipeline(self, transaction=True):
        if self._fail:
            raise ConnectionError("redis weg")
        return _FakePipe(self)

    async def rpush(self, key, value):
        self._s.setdefault(key, []).append(value)
        return len(self._s[key])

    async def ltrim(self, key, start, end):
        lst = self._s.get(key, [])
        n = len(lst)
        start = max(n + start, 0) if start < 0 else start
        end = n + end if end < 0 else end
        self._s[key] = lst[start:end + 1]
        return True

    async def lrange(self, key, start, end):
        lst = self._s.get(key, [])
        return lst[start:] if end == -1 else lst[start:end + 1]

    async def delete(self, key):
        return 1 if self._s.pop(key, None) is not None else 0

    async def expire(self, key, seconds):
        return key in self._s

    async def aclose(self):
        pass


def _run(coro):
    return asyncio.run(coro)


class MerkenUndAbholenTests(unittest.TestCase):
    def setUp(self):
        self.store = {}
        p = mock.patch.object(ausgang, "_client", lambda: _FakeRedis(self.store))
        p.start()
        self.addCleanup(p.stop)

    def test_vorspann_enthaelt_die_sendung_und_kommt_nur_einmal(self):
        _run(ausgang.merken("111", 42, "Artikel fertig: Staatsverschuldung und Gold"))
        vorspann = _run(ausgang.abholen("111", 42))
        self.assertIn("Artikel fertig: Staatsverschuldung und Gold", vorspann)
        self.assertIn("außerhalb deiner Chat-Antworten", vorspann)
        self.assertEqual(_run(ausgang.abholen("111", 42)), "")

    def test_getrennt_je_bot_und_chat(self):
        _run(ausgang.merken("111", 42, "für Chat 42"))
        self.assertEqual(_run(ausgang.abholen("111", 43)), "")
        self.assertEqual(_run(ausgang.abholen("222", 42)), "")
        self.assertIn("für Chat 42", _run(ausgang.abholen("111", 42)))

    def test_hoechstens_fuenf_gekuerzt(self):
        for i in range(8):
            _run(ausgang.merken("111", 42, f"Nachricht {i} " + "x" * 900))
        vorspann = _run(ausgang.abholen("111", 42))
        self.assertNotIn("Nachricht 2 ", vorspann)
        self.assertIn("Nachricht 3 ", vorspann)
        self.assertIn("Nachricht 7 ", vorspann)
        self.assertIn("[…]", vorspann)
        self.assertLess(len(vorspann), 5 * (ausgang.MAX_ZEICHEN + 20) + 300)

    def test_leeres_wird_nicht_gemerkt(self):
        _run(ausgang.merken("111", 42, "   "))
        _run(ausgang.merken("", 42, "x"))
        self.assertEqual(self.store, {})

    def test_beschreibung(self):
        self.assertEqual(ausgang.beschreibung("sendMessage", {"text": "Hallo"}), "Hallo")
        self.assertEqual(ausgang.beschreibung("sendDocument", {"caption": "Artikel.pdf"}),
                         "[Document] Artikel.pdf")
        self.assertEqual(ausgang.beschreibung("sendPhoto", None), "[Photo]")


class RedisFehlerTests(unittest.TestCase):
    def test_redis_fehler_stoert_nichts(self):
        with mock.patch.object(ausgang, "_client", lambda: _FakeRedis({}, fail=True)):
            _run(ausgang.merken("111", 42, "x"))  # wirft nicht
            self.assertEqual(_run(ausgang.abholen("111", 42)), "")


class AnschluesseTests(unittest.TestCase):
    """Die Stellen, an denen Sendungen ausserhalb des Gesprächs rausgehen."""

    def test_werkzeug_api_merkt_sendungen_aber_keine_entwuerfe(self):
        from app.api import telegram_actions as ta

        gemerkt = []

        async def fake_merken(bot_id, chat_id, text):
            gemerkt.append((bot_id, chat_id, text))

        class _Resp:
            def __init__(self, result):
                self._r = result

            def json(self):
                return {"ok": True, "result": self._r}

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, **k):
                return _Resp({"message_id": 9, "chat": {"id": 42}})

        async def nichts(*a, **k):
            return None

        with mock.patch.object(ta.httpx, "AsyncClient", _Client), \
                mock.patch("app.telegram.ausgang.merken", fake_merken), \
                mock.patch("app.telegram.chat_tail.note", nichts):
            _run(ta._tg_request("123:abc", "sendMessage", {"chat_id": 42, "text": "Artikel fertig"}))
            _run(ta._tg_request("123:abc", "sendRichMessageDraft", {"chat_id": 42, "text": "Entwurf"}))
            _run(ta._tg_request("123:abc", "getMe"))
        self.assertEqual(gemerkt, [("123", 42, "Artikel fertig")])

    def test_push_freigaben_und_beide_eingangswege_angeschlossen(self):
        src = _BOT_SRC.read_text()
        self.assertGreaterEqual(src.count("ausgang.abholen(self._bot_id, chat_id)"), 2)
        teil = src[src.index("async def send_to_all_authorized"):src.index("async def _listen_telegram_send")]
        self.assertIn("ausgang.merken(", teil)
        teil = src[src.index("async def _deliver_telegram_send"):src.index("async def _listen_responses")]
        self.assertIn("ausgang.merken(", teil)


@unittest.skipUnless(_REDIS_URL, "REDIS_URL nicht gesetzt")
class EchtesRedisTests(unittest.TestCase):
    def test_echtes_redis(self):
        import redis.asyncio as aioredis

        with mock.patch.object(ausgang, "_client",
                               lambda: aioredis.from_url(_REDIS_URL, decode_responses=True)):
            _run(ausgang.merken("test878", 1, "eins"))
            _run(ausgang.merken("test878", 1, "zwei"))
            v = _run(ausgang.abholen("test878", 1))
            self.assertIn("eins", v)
            self.assertIn("zwei", v)
            self.assertEqual(_run(ausgang.abholen("test878", 1)), "")


if __name__ == "__main__":
    unittest.main()
