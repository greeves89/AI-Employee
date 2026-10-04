"""Lizenzvorgabe und Verhalten der Software (#886).

Was die README zusagt, muss die Software auch tun — und umgekehrt darf ein
Lizenzzustand nie eine laufende Anlage anhalten. Geprueft wird mit echten,
signierten Lizenzen: ein Schluesselpaar nur fuer diesen Test ersetzt den
eingebauten oeffentlichen Schluessel.
"""

import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from app.core import license as lizenz
from app.core.agentenlimit import pruefe_agentenlimit

_SCHLUESSEL = Ed25519PrivateKey.generate()
_OEFFENTLICH = _SCHLUESSEL.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode()


def _b64(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).decode().rstrip("=")


def lizenzschluessel(tier="team", limit=10, tage=30, license_id="LIC-TEST") -> str:
    ablauf = (datetime.now(timezone.utc) + timedelta(days=tage)).isoformat() if tage is not None else None
    payload = json.dumps({
        "tier": tier, "issued_to": "Testkunde", "license_id": license_id,
        "instance_limit": limit, "expires_at": ablauf,
    }).encode()
    return f"{_b64(payload)}.{_b64(_SCHLUESSEL.sign(payload))}"


def _db(agenten: int):
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=agenten)))
    return db


class _MitEigenemSchluessel(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        p = patch.object(lizenz, "LICENSE_SERVER_PUBLIC_KEY", _OEFFENTLICH)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._zuruecksetzen)
        self._zuruecksetzen()

    @staticmethod
    def _zuruecksetzen():
        lizenz.load_license_from_string("")
        lizenz.merke_limit(0)


class LizenzLaden(_MitEigenemSchluessel):
    def test_gueltige_lizenz(self):
        lic = lizenz.load_license_from_string(lizenzschluessel())
        self.assertEqual((lic.zustand, lic.tier, lic.instance_limit), ("aktiv", "team", 10))
        self.assertEqual(lizenz.wirksames_agentenlimit(), (10, "lizenz"))

    def test_abgelaufene_lizenz_bleibt_geladen_und_behaelt_ihr_limit(self):
        """Bis #886 wurde sie zur Community-Lizenz — und damit unbegrenzt."""
        lic = lizenz.load_license_from_string(lizenzschluessel(tier="business", limit=30, tage=-1))
        self.assertEqual(lic.zustand, "abgelaufen")
        self.assertEqual(lic.tier, "business")
        self.assertEqual(lizenz.wirksames_agentenlimit(), (30, "lizenz"))
        self.assertFalse(lic.has_feature("sso_microsoft"), "abgelaufen = Funktionen auf Grundstand")
        self.assertTrue(lic.has_feature("multi_agent"))

    def test_gefaelschte_lizenz_zaehlt_nicht(self):
        echt = lizenzschluessel(limit=10)
        payload = json.dumps({"tier": "enterprise", "license_id": "X", "instance_limit": 0}).encode()
        gefaelscht = f"{_b64(payload)}.{echt.split('.')[1]}"
        lic = lizenz.load_license_from_string(gefaelscht)
        self.assertEqual(lic.zustand, "ohne")
        self.assertEqual(lic.tier, "community")

    def test_widerruf_vom_lizenzserver(self):
        lic = lizenz.load_license_from_string(lizenzschluessel(tier="business", limit=30))
        lizenz.setze_server_status("revoked")
        self.assertEqual(lic.zustand, "widerrufen")
        self.assertEqual(lizenz.wirksames_agentenlimit(), (30, "lizenz"))
        self.assertFalse(lic.has_feature("sso_microsoft"))

    def test_unbekannter_serverstatus_loest_nichts_aus(self):
        lic = lizenz.load_license_from_string(lizenzschluessel())
        for unsinn in ("REVOKED!!", "kaputt", None, 7):
            lizenz.setze_server_status(unsinn if isinstance(unsinn, str) else "")
            self.assertEqual(lic.zustand, "aktiv", unsinn)

    def test_ohne_lizenz_unbegrenzt(self):
        self.assertEqual(lizenz.wirksames_agentenlimit(), (0, None))

    def test_entfernte_lizenz_laesst_ihr_limit_zurueck(self):
        lizenz.load_license_from_string(lizenzschluessel(limit=10))
        lizenz.merke_limit(10)
        lizenz.load_license_from_string("")          # entfernt
        self.assertEqual(lizenz.get_current_license().zustand, "ohne")
        self.assertEqual(lizenz.wirksames_agentenlimit(), (10, "gemerkt"))

    def test_unbegrenzte_lizenz_bleibt_unbegrenzt(self):
        lizenz.load_license_from_string(lizenzschluessel(tier="enterprise", limit=0))
        self.assertEqual(lizenz.wirksames_agentenlimit(), (0, None))


class Agentenlimit(_MitEigenemSchluessel):
    async def _pruefe(self, agenten, zusaetzlich=1):
        await pruefe_agentenlimit(_db(agenten), zusaetzlich=zusaetzlich)

    async def test_unter_dem_limit(self):
        lizenz.load_license_from_string(lizenzschluessel(limit=10))
        await self._pruefe(9)

    async def test_am_limit_wird_abgelehnt(self):
        lizenz.load_license_from_string(lizenzschluessel(limit=10))
        with self.assertRaises(HTTPException) as fehler:
            await self._pruefe(10)
        self.assertEqual(fehler.exception.status_code, 402)
        self.assertEqual(fehler.exception.detail["error"], "agent_limit_reached")
        self.assertIn("10", fehler.exception.detail["message"])

    async def test_abgelaufen_gilt_das_limit_weiter(self):
        lizenz.load_license_from_string(lizenzschluessel(limit=3, tage=-5))
        with self.assertRaises(HTTPException):
            await self._pruefe(3)

    async def test_nach_dem_entfernen_gilt_das_limit_weiter(self):
        lizenz.merke_limit(3)
        with self.assertRaises(HTTPException):
            await self._pruefe(3)

    async def test_ohne_lizenz_wird_nicht_einmal_gezaehlt(self):
        db = _db(9999)
        await pruefe_agentenlimit(db)
        db.execute.assert_not_awaited()

    async def test_mehrere_auf_einmal_ganz_oder_gar_nicht(self):
        lizenz.load_license_from_string(lizenzschluessel(limit=10))
        await self._pruefe(7, zusaetzlich=3)
        with self.assertRaises(HTTPException) as fehler:
            await self._pruefe(8, zusaetzlich=3)
        self.assertIn("3 weitere", fehler.exception.detail["message"])


class JederAnlegeWeg(_MitEigenemSchluessel):
    """Alle Wege rufen ``AgentManager.create_agent`` — dort sitzt die Pruefung."""

    def _manager(self, agenten):
        from app.core.agent_manager import AgentManager

        docker = MagicMock()
        return AgentManager(_db(agenten), docker, MagicMock()), docker

    async def test_create_agent_lehnt_ab_bevor_ein_container_entsteht(self):
        lizenz.load_license_from_string(lizenzschluessel(limit=2))
        manager, docker = self._manager(agenten=2)
        with self.assertRaises(HTTPException) as fehler:
            await manager.create_agent(name="Neu", user_id="u1")
        self.assertEqual(fehler.exception.status_code, 402)
        docker.create_container.assert_not_called()

    async def test_vorlagen_weg_reicht_die_402_durch(self):
        """Der Endpunkt machte aus jeder HTTPException eine 500 mit Rohtext."""
        from app.api import templates as api

        absage = HTTPException(status_code=402, detail={"error": "agent_limit_reached", "message": "voll"})
        vorlage = SimpleNamespace(id=1, name="v", display_name="V", model="m", role="r",
                                  integrations=[], permissions=[], is_published=True)
        with patch.object(api, "vorlage_fuer_nutzer", AsyncMock(return_value=vorlage)), \
             patch.object(api, "AgentManager") as manager:
            manager.return_value.create_agent = AsyncMock(side_effect=absage)
            with self.assertRaises(HTTPException) as fehler:
                await api.create_agent_from_template(
                    1, api.CreateFromTemplate(), request=MagicMock(),
                    user=SimpleNamespace(id="__anonymous__", role=None),
                    db=MagicMock(), docker=MagicMock(), redis=MagicMock(),
                )
        self.assertEqual(fehler.exception.status_code, 402)

    async def test_branchenpaket_prueft_die_ganze_menge_vorab(self):
        from app.api import vertical_packs as api

        lizenz.load_license_from_string(lizenzschluessel(limit=10))
        paket = {"name": "P", "template_names": ["a", "b", "c"]}
        anlegen = AsyncMock()
        with patch.object(api, "get_pack", return_value=paket), patch.object(api, "provision_pack", anlegen):
            with self.assertRaises(HTTPException) as fehler:
                await api.provision_vertical_pack(
                    "p", user=SimpleNamespace(id="u1"), db=_db(8), docker=MagicMock(), redis=MagicMock(),
                )
        self.assertEqual(fehler.exception.status_code, 402)
        anlegen.assert_not_awaited()


class Hinweise(unittest.TestCase):
    """MCDC ueber Zustand, Belegung, Herkunft des Limits, Alter, Erklaerung."""

    def _h(self, **kw):
        basis = dict(zustand="ohne", agenten=0, limit=0, limit_quelle=None,
                     tage_seit_einrichtung=0, privat_erklaert=False)
        return lizenz.lizenz_hinweis(**{**basis, **kw})

    def test_alles_in_ordnung(self):
        self.assertIsNone(self._h())
        self.assertIsNone(self._h(zustand="aktiv", agenten=5, limit=10, limit_quelle="lizenz"))

    def test_widerrufen(self):
        self.assertIn("widerrufen", self._h(zustand="widerrufen", limit=10, limit_quelle="lizenz"))

    def test_abgelaufen(self):
        self.assertIn("abgelaufen", self._h(zustand="abgelaufen", limit=10, limit_quelle="lizenz"))

    def test_mehr_agenten_als_lizenziert(self):
        text = self._h(zustand="aktiv", agenten=20, limit=10, limit_quelle="lizenz")
        self.assertIn("20", text)
        self.assertIn("10", text)

    def test_genau_am_limit_ist_kein_hinweis(self):
        self.assertIsNone(self._h(zustand="aktiv", agenten=10, limit=10, limit_quelle="lizenz"))

    def test_limit_nach_dem_entfernen(self):
        self.assertIn("entfernt", self._h(agenten=2, limit=10, limit_quelle="gemerkt"))

    def test_testphase(self):
        self.assertIsNone(self._h(tage_seit_einrichtung=30), "am 30. Tag noch nicht")
        self.assertIn("31 Tagen", self._h(tage_seit_einrichtung=31))

    def test_private_nutzung_beendet_den_testphasen_hinweis(self):
        self.assertIsNone(self._h(tage_seit_einrichtung=400, privat_erklaert=True))

    def test_anlage_ohne_nutzer_hat_keine_testphase(self):
        self.assertIsNone(self._h(tage_seit_einrichtung=None))

    def test_jeder_hinweis_sagt_dass_nichts_gestoppt_wird_oder_ist_reine_auskunft(self):
        for kw in (dict(zustand="widerrufen"), dict(zustand="abgelaufen"),
                   dict(zustand="aktiv", agenten=20, limit=10, limit_quelle="lizenz")):
            self.assertIn("laufen weiter", self._h(**kw))


class LizenzApi(_MitEigenemSchluessel):
    def setUp(self):
        super().setUp()
        self.gespeichert: dict[str, str] = {}

        class _Einstellungen:
            def __init__(inner, _db):
                pass

            async def get(inner, key):
                return self.gespeichert.get(key)

            async def set(inner, key, value):
                from app.services.settings_service import ALLOWED_KEYS
                assert key in ALLOWED_KEYS, f"{key} fehlt in ALLOWED_KEYS — das Speichern wuerde still scheitern"
                self.gespeichert[key] = value

        p = patch("app.services.settings_service.SettingsService", _Einstellungen)
        p.start()
        self.addCleanup(p.stop)
        self.db = _db(4)
        self.db.commit = AsyncMock()
        self.admin = SimpleNamespace(id="a", role=__import__("app.models.user", fromlist=["UserRole"]).UserRole.ADMIN)

    async def test_falscher_schluessel_laesst_die_gueltige_lizenz_stehen(self):
        from app.api import license as api

        await api.apply_license(api.ApplyLicenseRequest(license_key=lizenzschluessel(limit=10)), self.admin, self.db)
        with self.assertRaises(HTTPException) as fehler:
            await api.apply_license(api.ApplyLicenseRequest(license_key="tippfehler"), self.admin, self.db)
        self.assertEqual(fehler.exception.status_code, 400)
        self.assertEqual(lizenz.get_current_license().zustand, "aktiv")

    async def test_entfernen_behaelt_das_limit_bis_zur_naechsten_lizenz(self):
        from app.api import license as api

        await api.apply_license(api.ApplyLicenseRequest(license_key=lizenzschluessel(limit=10)), self.admin, self.db)
        await api.remove_license(self.admin, self.db)
        self.assertEqual(self.gespeichert["license_key"], "")
        self.assertEqual(lizenz.wirksames_agentenlimit(), (10, "gemerkt"))

        await api.apply_license(
            api.ApplyLicenseRequest(license_key=lizenzschluessel(tier="business", limit=30, license_id="LIC-2")),
            self.admin, self.db)
        self.assertEqual(lizenz.wirksames_agentenlimit(), (30, "lizenz"))

    async def test_neustart_stellt_limit_und_serverstatus_wieder_her(self):
        from app.services import lizenz_zustand

        self.gespeichert.update({"license_key": lizenzschluessel(limit=10),
                                 "license_last_limit": "10", "license_server_status": "revoked"})
        self._zuruecksetzen()
        await lizenz_zustand.lade_lizenzzustand(self.db)
        self.assertEqual(lizenz.get_current_license().zustand, "widerrufen")
        self.assertEqual(lizenz.wirksames_agentenlimit(), (10, "lizenz"))

    async def test_neue_lizenz_loescht_den_serverstatus_der_alten(self):
        from app.api import license as api
        from app.services import lizenz_zustand

        await api.apply_license(api.ApplyLicenseRequest(license_key=lizenzschluessel()), self.admin, self.db)
        await lizenz_zustand.merke_server_status(self.db, "revoked")
        await api.apply_license(
            api.ApplyLicenseRequest(license_key=lizenzschluessel(license_id="LIC-NEU")), self.admin, self.db)
        self.assertEqual(lizenz.get_current_license().zustand, "aktiv")
        self.assertEqual(self.gespeichert["license_server_status"], "")

    async def test_status_fuer_admin_und_fuer_alle_anderen(self):
        from app.services import lizenz_zustand

        lizenz.load_license_from_string(lizenzschluessel(limit=3))
        with patch.object(lizenz_zustand, "_tage_seit_einrichtung", AsyncMock(return_value=5)):
            admin = await lizenz_zustand.lizenzstatus(self.db, fuer_admin=True)
            mitglied = await lizenz_zustand.lizenzstatus(self.db, fuer_admin=False)
        self.assertEqual((admin["agenten"], admin["agentenlimit"]), (4, 3))
        self.assertIn("4 Agenten", admin["hinweis"])
        for feld in ("agenten", "agentenlimit", "hinweis", "tage_seit_einrichtung"):
            self.assertNotIn(feld, mitglied)

    async def test_betreiber_hinweis_nur_fuer_admins_ueber_license(self):
        """#917: Der Hinweis des Anbieters (mit Kontaktadresse) geht nur an Admins."""
        from app.services import lizenz_zustand

        self.gespeichert["usage_ping_hinweis"] = "Bitte melden Sie sich unter kontakt@example.invalid"
        with patch.object(lizenz_zustand, "_tage_seit_einrichtung", AsyncMock(return_value=5)):
            admin = await lizenz_zustand.lizenzstatus(self.db, fuer_admin=True)
            mitglied = await lizenz_zustand.lizenzstatus(self.db, fuer_admin=False)
        self.assertIn("kontakt@example.invalid", admin["betreiber_hinweis"])
        self.assertNotIn("betreiber_hinweis", mitglied)
        self.assertNotIn("kontakt@example.invalid", json.dumps(mitglied, default=str))

    async def test_version_ohne_anmeldung_verraet_keinen_hinweis(self):
        """#917: ``GET /version/`` ist oeffentlich — dort darf der Hinweis nicht stehen."""
        from app.api import version as api

        self.gespeichert["usage_ping_hinweis"] = "Bitte melden Sie sich unter kontakt@example.invalid"
        with patch.object(api, "_fetch_latest_version", AsyncMock(return_value=None)):
            antwort = await api.check_version()
        self.assertNotIn("betreiber_hinweis", antwort)
        self.assertNotIn("kontakt@example.invalid", json.dumps(antwort, default=str))

    async def test_erklaerung_zur_privaten_nutzung(self):
        from app.api import license as api
        from app.services import lizenz_zustand

        with patch.object(lizenz_zustand, "_tage_seit_einrichtung", AsyncMock(return_value=90)):
            vorher = await lizenz_zustand.lizenzstatus(self.db, fuer_admin=True)
            nachher = await api.set_nutzung(api.NutzungRequest(privat=True), self.admin, self.db)
        self.assertIn("90 Tagen", vorher["hinweis"])
        self.assertIsNone(nachher["hinweis"])
        self.assertTrue(nachher["private_nutzung"])


if __name__ == "__main__":
    unittest.main()
