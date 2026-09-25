"""Das taegliche Lebenszeichen — Takt, Inhalt und Harmlosigkeit.

Warum es das gibt: Der lizenzierte Herzschlag verlangt eine eingetragene
Serveradresse UND einen Lizenzschluessel. Damit meldet sich nur, wer ohnehin
schon Kunde ist — die Installationen, um die es geht, sieht der Betreiber nie.

Der Takt ist eine bewusste Entscheidung und wird hier festgehalten:

* **Einmal am Tag.** Die Frage lautet "wer setzt das ein", nicht "was tut er
  gerade". Haeufiger waere schwer zu rechtfertigen — es ist keine Funktion,
  von der der Betreiber der Anlage etwas hat.
* **Erster Ping kurz nach dem Start.** Wer die Plattform nur einen Nachmittag
  ausprobiert, taucht sonst nie auf.
* **Mit Streuung.** Ohne sie klopfen nach einer gemeinsamen Neustartwelle
  (Stromausfall, Update) tausend Anlagen im selben Moment an.

Und drei Dinge, die es NICHT tun darf: den Start verzoegern, bei einem
unerreichbaren Server lautstark scheitern, oder irgendetwas sperren.
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.services import license_heartbeat_service as dienst


class _Db:
    """Sitzungs-Attrappe — der Dienst benutzt sie nur als Kontextmanager."""

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def commit(self): pass


class TaktTest(unittest.TestCase):
    """Die gewaehlten Zahlen — und warum sie so stehen."""

    def test_einmal_am_tag(self):
        self.assertEqual(dienst.PING_INTERVALL, 24 * 3600)

    def test_der_erste_kommt_frueh_aber_nicht_sofort(self):
        """Kurzlebige Installationen sollen auftauchen; der Start hat Vorrang."""
        self.assertGreaterEqual(dienst.PING_START_VERZUG, 60)
        self.assertLessEqual(dienst.PING_START_VERZUG, 600)

    def test_der_lizenzierte_herzschlag_bleibt_haeufiger(self):
        """Ein Lizenzentzug muss zeitnah ankommen — das Lebenszeichen nicht."""
        self.assertLess(dienst._INTERVAL, dienst.PING_INTERVALL)

    def test_die_streuung_verteilt_wirklich(self):
        """Nachgemessen, nicht behauptet."""
        werte = {round(dienst._mit_streuung(1000), 3) for _ in range(200)}
        self.assertGreater(len(werte), 150, "Die Wartezeit streut kaum.")
        self.assertTrue(all(900 <= w <= 1100 for w in werte),
                        "Die Streuung liegt ausserhalb der erwarteten 10 %.")

    def test_die_streuung_wird_nie_negativ(self):
        """Eine negative Wartezeit waere eine Endlosschleife."""
        self.assertGreaterEqual(dienst._mit_streuung(2), 1.0)


class InhaltTest(unittest.IsolatedAsyncioTestCase):
    """Was gesendet wird — und was ausdruecklich nicht."""

    def _service(self, einstellungen):
        class _Svc:
            def __init__(self, _db): pass
            async def get(self, k): return einstellungen.get(k)
            async def set(self, k, v): einstellungen[k] = v

        s = dienst.LicenseHeartbeatService(lambda: _Db())
        return s, _Svc

    async def _ping(self, einstellungen, antwort=None, status=200):
        s, svc = self._service(einstellungen)
        gesendet = {}

        class _Resp:
            status_code = status
            def json(self_inner): return antwort or {}

        class _Client:
            async def __aenter__(self_inner): return self_inner
            async def __aexit__(self_inner, *a): return False
            async def post(self_inner, url, json=None, headers=None):
                gesendet["url"] = url
                gesendet["body"] = json
                return _Resp()

        with patch("app.db.session.resilient_session", lambda session_factory: _Db()), \
             patch("app.services.settings_service.SettingsService", svc), \
             patch.object(dienst.httpx, "AsyncClient", lambda **k: _Client()):
            await s._ping()
        return gesendet

    async def test_es_wird_nur_kennung_und_version_gesendet(self):
        """Kein Inhalt, keine Namen, keine Agentendaten."""
        gesendet = await self._ping({"license_instance_id": "abc123"})
        self.assertEqual(set(gesendet["body"]), {"instance_id", "version"})
        self.assertEqual(gesendet["body"]["instance_id"], "abc123")

    async def test_ohne_kennung_wird_eine_erzeugt_und_gemerkt(self):
        einstellungen: dict = {}
        gesendet = await self._ping(einstellungen)
        self.assertTrue(gesendet["body"]["instance_id"])
        self.assertEqual(einstellungen["license_instance_id"],
                         gesendet["body"]["instance_id"],
                         "Die Kennung muss stabil bleiben, sonst zaehlt jede "
                         "Meldung als neue Installation.")

    async def test_der_betreiber_kann_es_abschalten(self):
        gesendet = await self._ping({"usage_ping_enabled": "false"})
        self.assertEqual(gesendet, {}, "Trotz Abschaltung gesendet.")

    async def test_der_hinweis_aus_der_antwort_wird_gemerkt(self):
        einstellungen = {"license_instance_id": "x"}
        await self._ping(einstellungen,
                         antwort={"bewertung": "bitte_melden", "hinweis": "Bitte melden."})
        self.assertEqual(einstellungen["usage_ping_hinweis"], "Bitte melden.")
        self.assertEqual(einstellungen["usage_ping_bewertung"], "bitte_melden")

    async def test_ein_zurueckgenommener_hinweis_verschwindet_wieder(self):
        """Sonst bliebe ein einmal gesetzter Streifen fuer immer stehen."""
        einstellungen = {"license_instance_id": "x", "usage_ping_hinweis": "alt"}
        await self._ping(einstellungen, antwort={"bewertung": "legitim"})
        self.assertEqual(einstellungen["usage_ping_hinweis"], "")

    async def test_ein_unerreichbarer_server_ist_folgenlos(self):
        """Weder Ausnahme noch Zustandsaenderung."""
        einstellungen = {"license_instance_id": "x", "usage_ping_hinweis": "alt"}
        s, svc = self._service(einstellungen)

        class _Client:
            async def __aenter__(self_inner): return self_inner
            async def __aexit__(self_inner, *a): return False
            async def post(self_inner, *a, **k):
                raise dienst.httpx.ConnectError("kein Netz")

        with patch("app.db.session.resilient_session", lambda session_factory: _Db()), \
             patch("app.services.settings_service.SettingsService", svc), \
             patch.object(dienst.httpx, "AsyncClient", lambda **k: _Client()):
            await s._ping()          # darf nicht werfen
        self.assertEqual(einstellungen["usage_ping_hinweis"], "alt",
                         "Ein Netzfehler darf den Zustand nicht veraendern.")


if __name__ == "__main__":
    unittest.main()
