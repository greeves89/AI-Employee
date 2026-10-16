"""Ein kurzer Netzaussetzer darf keine Meldung an den Nutzer verschlucken.

Gemessener Anlass: ein Namensaufloesungs-Aussetzer von zwei Sekunden auf einer
Kundenanlage; im Fehlerlog genau eine Zeile ("APNs send failed"), auf dem Telefon
nichts. Der Push war weg, weil beide Zustellwege genau einen Versuch machten.

Geprueft wird deshalb nicht nur, DASS wiederholt wird, sondern auch die Grenze:
ein Lesefehler (Anfrage kann angekommen sein) darf NICHT wiederholt werden, sonst
steht dieselbe Meldung zweimal auf dem Geraet.
"""

import asyncio
import unittest

import httpx

from app.core import webpush
from app.core.verbindungswiederholung import (
    PAUSEN_S,
    mit_verbindungswiederholung,
)


def _lauf(coro):
    return asyncio.run(coro)


class HelferTests(unittest.TestCase):
    """Die Regel selbst: wiederholen genau dann, wenn nichts abgeschickt wurde."""

    def test_verbindungsfehler_wird_wiederholt_und_der_zweite_versuch_zaehlt(self):
        versuche = []

        async def senden():
            versuche.append(1)
            if len(versuche) == 1:
                raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
            return "zugestellt"

        ergebnis = _lauf(mit_verbindungswiederholung(
            senden, zweck="Test", pausen=(0.0,),
        ))
        self.assertEqual("zugestellt", ergebnis)
        self.assertEqual(2, len(versuche))

    def test_dauerhafter_verbindungsfehler_gibt_die_ausnahme_weiter(self):
        versuche = []

        async def senden():
            versuche.append(1)
            raise httpx.ConnectError("kein DNS")

        with self.assertRaises(httpx.ConnectError):
            _lauf(mit_verbindungswiederholung(
                senden, zweck="Test", pausen=(0.0, 0.0),
            ))
        # Erstversuch + zwei Wiederholungen, nicht mehr.
        self.assertEqual(3, len(versuche))

    def test_lesefehler_wird_NICHT_wiederholt_sonst_kommt_die_meldung_doppelt(self):
        """Die eigentliche Schutzregel: bei ReadTimeout kann die Anfrage angekommen
        sein. Ein zweiter Versuch wuerde dieselbe Meldung erneut auf dem Geraet
        erscheinen lassen."""
        versuche = []

        async def senden():
            versuche.append(1)
            raise httpx.ReadTimeout("Antwort blieb aus")

        with self.assertRaises(httpx.ReadTimeout):
            _lauf(mit_verbindungswiederholung(
                senden, zweck="Test", pausen=(0.0, 0.0),
            ))
        self.assertEqual(1, len(versuche))

    def test_eine_abgelehnte_antwort_ist_kein_verbindungsfehler(self):
        """Ein 400/410 vom Push-Dienst ist eine Antwort, kein Transportfehler —
        der Aufrufer wertet sie aus, wiederholt wird nichts."""
        versuche = []

        async def senden():
            versuche.append(1)
            return httpx.Response(410)

        antwort = _lauf(mit_verbindungswiederholung(senden, zweck="Test"))
        self.assertEqual(410, antwort.status_code)
        self.assertEqual(1, len(versuche))

    def test_zwischen_den_versuchen_wird_gewartet(self):
        """Ohne Pause laufen alle Versuche in dieselbe Aussetzer-Millisekunde."""
        gewartet: list[float] = []
        echtes_sleep = asyncio.sleep

        async def ohne_warten(s):
            gewartet.append(s)
            await echtes_sleep(0)

        async def senden():
            raise httpx.ConnectError("kein DNS")

        from unittest.mock import patch
        with patch("app.core.verbindungswiederholung.asyncio.sleep", ohne_warten):
            with self.assertRaises(httpx.ConnectError):
                _lauf(mit_verbindungswiederholung(senden, zweck="Test"))
        self.assertEqual(list(PAUSEN_S), gewartet)
        self.assertGreaterEqual(sum(PAUSEN_S), 1.0,
                                "Die Deckung muss die gemessenen Aussetzer (2-5 s) "
                                "wenigstens anschneiden")


class _FakeClient:
    """httpx-Client, der beim ersten Versuch die Verbindung verweigert."""

    def __init__(self, antworten):
        self._antworten = list(antworten)
        self.aufrufe = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, *_args, **_kwargs):
        self.aufrufe += 1
        naechste = self._antworten.pop(0)
        if isinstance(naechste, BaseException):
            raise naechste
        return naechste


class ZustellwegTests(unittest.TestCase):
    """Beide Wege, die push_to_user auffaechert, muessen die Regel benutzen."""

    def test_apns_stellt_nach_einem_dns_aussetzer_doch_zu(self):
        from unittest.mock import patch

        from app.services.apns_service import APNsService

        client = _FakeClient([
            httpx.ConnectError("[Errno -3] Temporary failure in name resolution"),
            httpx.Response(200),
        ])
        with patch.object(APNsService, "configured", classmethod(lambda cls: True)), \
             patch.object(APNsService, "_provider_token", classmethod(lambda cls: "jwt")), \
             patch("app.services.apns_service.httpx.AsyncClient", lambda **_: client), \
             patch("app.core.verbindungswiederholung.PAUSEN_S", (0.0, 0.0)):
            ok = _lauf(APNsService.send("devicetoken", "Titel", "Text"))
        self.assertTrue(ok, "Der Push war nach einem kurzen Aussetzer verloren")
        self.assertEqual(2, client.aufrufe)

    def test_web_push_stellt_nach_einem_dns_aussetzer_doch_zu(self):
        from unittest.mock import patch

        client = _FakeClient([
            httpx.ConnectError("[Errno -3] Temporary failure in name resolution"),
            httpx.Response(201),
        ])
        keys = webpush.VapidKeys.generate()
        with patch("app.core.webpush.httpx.AsyncClient", lambda **_: client), \
             patch("app.core.verbindungswiederholung.PAUSEN_S", (0.0, 0.0)):
            status = _lauf(webpush.send(
                endpoint="https://push.example.com/abc",
                p256dh=_empfaenger_pub(), auth=_empfaenger_auth(),
                payload=b'{"title":"x"}', keys=keys,
                subject="mailto:betreiber@example.com",
            ))
        self.assertEqual(201, status)
        self.assertEqual(2, client.aufrufe)


class AusnahmemengeTests(unittest.TestCase):
    """Die Grenze der Menge festnageln — in BEIDE Richtungen.

    Ohne diese Tabelle kann ein Aufraeum-Commit die halbe Menge entfernen
    (z. B. ConnectTimeout, also genau den Fall "Netz schluckt Pakete"), ohne dass
    ein Test rot wird.
    """

    WIRD_WIEDERHOLT = [
        httpx.ConnectError("[Errno -3] Temporary failure in name resolution"),
        httpx.ConnectTimeout("Verbindung kam nie zustande"),
        httpx.PoolTimeout("keine Verbindung aus dem Pool"),
        httpx.ProxyError("CONNECT abgelehnt"),
    ]
    WIRD_NICHT_WIEDERHOLT = [
        httpx.ReadTimeout("Antwort blieb aus"),
        httpx.ReadError("Verbindung waehrend des Lesens weg"),
        httpx.WriteError("Verbindung waehrend des Schreibens weg"),
        httpx.RemoteProtocolError("Gegenseite spricht kaputt"),
    ]

    def _versuche_bis_zum_abbruch(self, fehler):
        versuche = []

        async def senden():
            versuche.append(1)
            raise fehler

        with self.assertRaises(type(fehler)):
            _lauf(mit_verbindungswiederholung(
                senden, zweck="Test", pausen=(0.0, 0.0),
            ))
        return len(versuche)

    def test_verbindung_kam_nie_zustande_also_drei_versuche(self):
        for fehler in self.WIRD_WIEDERHOLT:
            with self.subTest(fehler=type(fehler).__name__):
                self.assertEqual(3, self._versuche_bis_zum_abbruch(fehler))

    def test_bytes_koennten_draussen_sein_also_genau_ein_versuch(self):
        for fehler in self.WIRD_NICHT_WIEDERHOLT:
            with self.subTest(fehler=type(fehler).__name__):
                self.assertEqual(1, self._versuche_bis_zum_abbruch(fehler))


class ZustellbudgetTests(unittest.TestCase):
    """Der Deckel ueber der ganzen Auffaecherung.

    Die Wiederholungen verdreifachen im Stoerfall die Wartezeit je Geraet, und zwei
    Aufrufer warten im HTTP-Pfad darauf. Ohne Deckel addieren sich die Zeitlimits
    aller Geraete eines Nutzers.
    """

    def test_bei_haengendem_netz_bricht_die_auffaecherung_ab(self):
        import time
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from app.core import push
        from app.services.apns_service import APNsService

        versucht = []

        async def haengt(token, *_a, **_k):
            versucht.append(token)
            await asyncio.sleep(30)
            return True

        class _Ergebnis:
            def __init__(self, objekte):
                self._o = objekte

            def scalars(self):
                return self

            def all(self):
                return self._o

        class _Db:
            def __init__(self, geraete):
                self._g = geraete

            async def execute(self, _stmt):
                return _Ergebnis(self._g)

        geraete = [SimpleNamespace(token=f"t{i}") for i in range(5)]

        async def messen():
            start = time.monotonic()
            await push.push_to_user(_Db(geraete), "nutzer-1", "Titel", "Text")
            return time.monotonic() - start

        with patch.object(push, "ZUSTELLBUDGET_S", 0.3), \
             patch.object(push, "_push_web", AsyncMock()), \
             patch.object(APNsService, "configured", classmethod(lambda cls: True)), \
             patch.object(APNsService, "send", haengt):
            dauer = _lauf(messen())

        self.assertLess(dauer, 2.0,
                        f"Die Auffaecherung lief {dauer:.1f}s — der Deckel greift nicht")
        self.assertLess(len(versucht), len(geraete),
                        "Alle Geraete wurden versucht, obwohl das Budget aufgebraucht war")
        self.assertGreaterEqual(len(versucht), 1,
                                "Kein einziges Geraet versucht — der Deckel ist zu scharf")

    def test_der_deckel_selbst_bleibt_in_einem_brauchbaren_bereich(self):
        """Der Test oben setzt das Budget selbst, kann also einen kaputten
        Standardwert nicht sehen. Bei 0 kaeme keine einzige Meldung mehr an, bei
        einem sehr grossen Wert ist der HTTP-Pfad wieder so lange blockiert wie
        vorher — beide Richtungen werden hier festgenagelt."""
        from app.core import push

        self.assertGreaterEqual(push.ZUSTELLBUDGET_S, 5.0,
                                "Zu knapp: deckt nicht einmal einen Aussetzer auf einem Geraet")
        self.assertLessEqual(push.ZUSTELLBUDGET_S, 30.0,
                             "Zu weit: der Meldungs-Aufruf haengt wieder minutennah")


# Hilfsmittel fuer den Web-Push-Fall: echte Empfaenger-Schluessel, sonst scheitert
# die Verschluesselung noch vor dem Senden und der Test prueft nichts.
def _empfaenger():
    import os

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return pub, os.urandom(16)


_EMPF = _empfaenger()


def _b64(b: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _empfaenger_pub() -> str:
    return _b64(_EMPF[0])


def _empfaenger_auth() -> str:
    return _b64(_EMPF[1])


if __name__ == "__main__":
    unittest.main()
