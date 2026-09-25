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

import hashlib
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.services import license_heartbeat_service as dienst

#: Alles, was das Lebenszeichen enthalten DARF. Wer hier etwas ergaenzt,
#: muss die Datenschutzerklaerung (docs/ios-app/datenschutz.html) mitziehen —
#: die Liste ist das Versprechen an die Betreiber der Anlagen.
ERLAUBTE_FELDER = {"instance_id", "version", "agent_count", "license_key_hash"}


class _Ergebnis:
    def __init__(self, wert): self._wert = wert
    def scalar(self): return self._wert


class _Db:
    """Sitzungs-Attrappe. ``execute`` beantwortet nur die Agentenzaehlung."""

    #: Alles, was waehrend eines Tests per ``add`` angelegt wurde.
    angelegt: list = []

    def __init__(self, agenten: int = 0): self._agenten = agenten
    def add(self, objekt): _Db.angelegt.append(objekt)
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def commit(self): pass
    async def execute(self, _anfrage): return _Ergebnis(self._agenten)


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
        from app.services.settings_service import ALLOWED_KEYS

        class _Svc:
            """So streng wie das Original.

            Die erste Fassung dieser Attrappe nahm jeden Schluessel an. Der echte
            SettingsService lehnt unbekannte ab — und genau daran ist das
            Speichern des Hinweises gescheitert, auf jeder Anlage, still. Die
            Tests waren trotzdem gruen.
            """
            def __init__(self, _db): pass
            async def get(self, k): return einstellungen.get(k)
            async def set(self, k, v):
                if k not in ALLOWED_KEYS:
                    raise ValueError(f"Unknown setting: {k}")
                einstellungen[k] = v

        s = dienst.LicenseHeartbeatService(lambda: _Db())
        return s, _Svc

    async def _ping(self, einstellungen, antwort=None, status=200, agenten=0):
        _Db.angelegt = []
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

        with patch("app.db.session.resilient_session", lambda session_factory: _Db(agenten)), \
             patch("app.services.settings_service.SettingsService", svc), \
             patch.object(dienst.httpx, "AsyncClient", lambda **k: _Client()):
            await s._ping()
        return gesendet

    async def test_gesendet_wird_nur_was_ausdruecklich_erlaubt_ist(self):
        """Kein Inhalt, keine Namen — nur Bestand, nie Verhalten."""
        gesendet = await self._ping({"license_instance_id": "abc123",
                                     "license_key": "irgendein-token"})
        self.assertLessEqual(set(gesendet["body"]), ERLAUBTE_FELDER,
                             "Das Lebenszeichen enthaelt ein nicht freigegebenes Feld.")
        self.assertEqual(gesendet["body"]["instance_id"], "abc123")

    async def test_die_agentenzahl_geht_als_blosse_zahl_mit(self):
        gesendet = await self._ping({"license_instance_id": "x"}, agenten=4)
        self.assertEqual(gesendet["body"]["agent_count"], 4)

    async def test_der_schluessel_selbst_verlaesst_die_anlage_nie(self):
        """Der Endpunkt ist offen — dort hat ein Schluessel nichts verloren."""
        schluessel = "eyJ-ein-signierter-lizenzschluessel"
        gesendet = await self._ping({"license_instance_id": "x", "license_key": schluessel})
        self.assertNotIn(schluessel, json.dumps(gesendet["body"]))
        self.assertEqual(gesendet["body"]["license_key_hash"],
                         hashlib.sha256(schluessel.encode("utf-8")).hexdigest(),
                         "Der Lizenzserver hasht das ausgegebene Token genauso — "
                         "sonst findet der Abgleich keinen Kunden.")

    async def test_ohne_schluessel_kein_hash(self):
        gesendet = await self._ping({"license_instance_id": "x"})
        self.assertNotIn("license_key_hash", gesendet["body"])

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

    def _benachrichtigungen(self):
        from app.models.notification import Notification
        return [o for o in _Db.angelegt if isinstance(o, Notification)]

    async def test_ein_neuer_hinweis_landet_bei_den_administratoren(self):
        """Den Streifen kann man wegklicken — die Benachrichtigung bleibt."""
        await self._ping({"license_instance_id": "x"},
                         antwort={"bewertung": "bitte_melden", "hinweis": "Bitte melden."})
        meldungen = self._benachrichtigungen()
        self.assertEqual(len(meldungen), 1)
        self.assertEqual(meldungen[0].agent_id, "system",
                         "Nur der Absender 'system' erreicht die Administratoren.")
        self.assertIn("Bitte melden.", meldungen[0].message)

    async def test_derselbe_hinweis_wird_nicht_taeglich_neu_gemeldet(self):
        await self._ping({"license_instance_id": "x", "usage_ping_hinweis": "Bitte melden."},
                         antwort={"bewertung": "bitte_melden", "hinweis": "Bitte melden."})
        self.assertEqual(self._benachrichtigungen(), [])

    async def test_ein_geaenderter_hinweis_wird_gemeldet(self):
        await self._ping({"license_instance_id": "x", "usage_ping_hinweis": "Alt."},
                         antwort={"bewertung": "bitte_melden", "hinweis": "Neu."})
        self.assertEqual(len(self._benachrichtigungen()), 1)

    async def test_ohne_hinweis_keine_benachrichtigung(self):
        await self._ping({"license_instance_id": "x", "usage_ping_hinweis": "Alt."},
                         antwort={"bewertung": "legitim"})
        self.assertEqual(self._benachrichtigungen(), [])

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
