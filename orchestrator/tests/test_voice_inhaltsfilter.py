"""Der Inhaltsfilter des Sprach-Anbieters sperrt nicht mehr den ganzen Agenten.

Befund 07.10.2026: Jede Sprachsitzung eines Agenten wurde schon beim Aufbau
blockiert — auch leere, auch nach einem Gesprächswechsel. Ursache: zwei harmlose
Einträge im Gedächtnisblock, der bei JEDER Sitzung mitgeschickt wird.

    Lage                                             Verhalten
    Block beim Aufbau, normale Sitzung               schlank neu verbinden (retryable), Ursache suchen
    Block beim Aufbau, schon schlanke Sitzung        endgültige Meldung (keine Schleife)
    Block mitten im Gespräch                         endgültige Meldung „neues Gespräch“
    schlanker Start gemerkt                          kein Gedächtnis, kein Verlauf
    beanstandete Einträge bekannt                    nur diese fehlen, der Rest ist da
    Prüfung eines Eintrags scheitert technisch       zählt NICHT als beanstandet
"""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core import voice_inhaltsfilter as vf
from app.core.memory_preload import eintrag_zeile

BLOCK = "RequestId=x : Error(s):\nError 1 : This request has been blocked by our content filters."


class _Redis:
    def __init__(self):
        self.d = {}

    async def exists(self, k):
        return 1 if k in self.d else 0

    async def set(self, k, v, ex=None):
        self.d[k] = v

    async def get(self, k):
        return self.d.get(k)

    async def delete(self, k):
        self.d.pop(k, None)


class MerkenUndLesen(unittest.IsolatedAsyncioTestCase):
    async def test_schlanker_start_wird_gemerkt(self):
        r = _Redis()
        self.assertFalse(await vf.ohne_gedaechtnis(r, "a1"))
        await vf.ohne_gedaechtnis_merken(r, "a1")
        self.assertTrue(await vf.ohne_gedaechtnis(r, "a1"))
        self.assertFalse(await vf.ohne_gedaechtnis(r, "a2"), "nur dieser Agent")

    async def test_beanstandete_merken_hebt_den_schlanken_start_auf(self):
        r = _Redis()
        await vf.ohne_gedaechtnis_merken(r, "a1")
        await vf.gesperrte_merken(r, "a1", ["eintrag_x"])
        self.assertEqual(await vf.gesperrte(r, "a1"), frozenset({"eintrag_x"}))
        self.assertFalse(await vf.ohne_gedaechtnis(r, "a1"), "der Rest des Gedächtnisses kommt zurück")
        await vf.gesperrte_merken(r, "a1", ["eintrag_y"])
        self.assertEqual(await vf.gesperrte(r, "a1"), frozenset({"eintrag_x", "eintrag_y"}))

    async def test_ohne_redis_lieber_mit_gedaechtnis(self):
        kaputt = MagicMock()
        kaputt.exists = AsyncMock(side_effect=RuntimeError("weg"))
        kaputt.get = AsyncMock(side_effect=RuntimeError("weg"))
        self.assertFalse(await vf.ohne_gedaechtnis(kaputt, "a1"))
        self.assertEqual(await vf.gesperrte(kaputt, "a1"), frozenset())


class EinzelnPruefen(unittest.IsolatedAsyncioTestCase):
    EINTRAEGE = [
        {"key": "gut", "category": "learning", "content": "harmlos"},
        {"key": "boese", "category": "decision", "content": "wird beanstandet"},
        {"key": "wackelig", "category": "learning", "content": "Netzfehler bei der Prüfung"},
        {"key": "", "category": "learning", "content": "ohne Schlüssel"},
    ]

    async def test_nur_beanstandete_und_fehler_zaehlen_nicht(self):
        async def pruefer(text):
            if "Netzfehler" in text:
                raise TimeoutError("Anbieter antwortet nicht")
            return "beanstandet" in text

        self.assertEqual(await vf.beanstandete_finden(self.EINTRAEGE, pruefer), ["boese"])

    async def test_geprueft_wird_die_zeile_wie_sie_im_prompt_steht(self):
        gesehen = []

        async def pruefer(text):
            gesehen.append(text)
            return False

        await vf.beanstandete_finden(self.EINTRAEGE[:1], pruefer)
        self.assertEqual(gesehen, [eintrag_zeile(self.EINTRAEGE[0])])


class GedaechtnisblockOhneBeanstandete(unittest.IsolatedAsyncioTestCase):
    async def test_nur_die_gesperrten_fehlen_und_andere_ruecken_nach(self):
        from app.core import memory_preload as mp

        daten = {"critical": [{"key": f"k{i}", "category": "learning", "content": f"Inhalt {i}"} for i in range(14)],
                 "recent_learnings": []}
        with patch.object(mp, "collect_preload", AsyncMock(return_value=daten)):
            alle = await mp.as_prompt_block(None, "a1", limit=12)
            ohne = await mp.as_prompt_block(None, "a1", limit=12, ohne_schluessel=frozenset({"k3", "k7"}))
        self.assertIn("k3:", alle)
        self.assertNotIn("k3:", ohne)
        self.assertNotIn("k7:", ohne)
        self.assertIn("k12:", ohne, "ein anderer Eintrag rückt nach")
        self.assertEqual(ohne.count("\n  - "), 12)


class SitzungReagiertAufDenBlock(unittest.IsolatedAsyncioTestCase):
    def _sitzung(self, *, schlank=False, gesprochen=False):
        from app.services.realtime_voice_session import RealtimeVoiceSession

        s = object.__new__(RealtimeVoiceSession)
        s.agent_id, s.session_id, s._planned = "a1", "s1", []
        s._schlank = schlank
        s._last_user_ts = 5.0 if gesprochen else 0.0
        s.redis = MagicMock(client=_Redis())
        s._beanstandete_eintraege_finden = AsyncMock()
        s.gesendet = []

        async def emit(evt):
            s.gesendet.append(evt)

        s._emit = emit
        return s

    async def test_block_beim_aufbau_verbindet_schlank_neu(self):
        import asyncio

        s = self._sitzung()
        await s._on_nova_event("error", {"message": BLOCK})
        await asyncio.sleep(0)
        daten = s.gesendet[0]["data"]
        self.assertTrue(daten["retryable"], "der Client verbindet neu")
        self.assertEqual(daten["reason"], "content_filter_start")
        self.assertTrue(await vf.ohne_gedaechtnis(s.redis.client, "a1"))
        s._beanstandete_eintraege_finden.assert_called_once()

    async def test_schlanke_sitzung_laeuft_nicht_im_kreis(self):
        s = self._sitzung(schlank=True)
        await s._on_nova_event("error", {"message": BLOCK})
        daten = s.gesendet[0]["data"]
        self.assertFalse(daten["retryable"])
        self.assertEqual(daten["reason"], "content_filter")
        s._beanstandete_eintraege_finden.assert_not_called()

    async def test_block_mitten_im_gespraech_bleibt_endgueltig(self):
        s = self._sitzung(gesprochen=True)
        await s._on_nova_event("error", {"message": BLOCK})
        self.assertFalse(s.gesendet[0]["data"]["retryable"])
        self.assertFalse(await vf.ohne_gedaechtnis(s.redis.client, "a1"))


class UrsachensucheMerktDieEintraege(unittest.IsolatedAsyncioTestCase):
    async def test_beanstandete_werden_gemerkt(self):
        from app.services import realtime_voice_session as rvs

        s = object.__new__(rvs.RealtimeVoiceSession)
        s.agent_id = "a1"
        s._creds_fuer_pruefung = {"engine": "nova_sonic", "region": "r", "access_key": "k", "secret_key": "s"}
        s.redis = MagicMock(client=_Redis())
        await vf.ohne_gedaechtnis_merken(s.redis.client, "a1")
        eintraege = [{"key": "gut", "category": "l", "content": "ok"},
                     {"key": "boese", "category": "l", "content": "schlimm"}]

        class _Sitzung:
            async def __aenter__(self_):
                return None

            async def __aexit__(self_, *a):
                return False

        async def blockiert(creds, text, warte_sekunden=10.0):
            return "schlimm" in text

        with patch("app.core.memory_preload.sprach_eintraege", AsyncMock(return_value=eintraege)), \
                patch("app.db.session.async_session_factory", lambda: _Sitzung()), \
                patch.object(rvs, "_anbieter_blockiert", blockiert):
            await s._beanstandete_eintraege_finden()
        self.assertEqual(json.loads(s.redis.client.d["voice:filter:gesperrt:a1"]), ["boese"])
        self.assertFalse(await vf.ohne_gedaechtnis(s.redis.client, "a1"))


if __name__ == "__main__":
    unittest.main()
