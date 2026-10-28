"""Browser-Schluessel je Agent (#828, Schritt 5).

Der Orchestrator gibt jedem Agenten zwei Schluessel mit: einen fuer das
gesicherte Browserprofil und einen Ausweis, ohne den der Agent Bildstrom und
Pruefprotokoll nicht herausgibt. Beide landen in der Umgebung des Agenten und
sind dort fuer ihn lesbar. Deshalb muessen sie je Agent verschieden sein und
duerfen weder ``ENCRYPTION_KEY`` noch den Schluessel eines anderen Agenten
verraten.
"""

import base64
import unittest
from unittest import mock

from cryptography.fernet import Fernet


class AgentSchluessel(unittest.TestCase):
    def setUp(self):
        from app.core import encryption

        self.encryption = encryption
        self.stamm = Fernet.generate_key().decode()
        self._patch = mock.patch.object(encryption, "_resolve_key", return_value=self.stamm)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_stabil_und_ein_gueltiger_fernet_schluessel(self):
        a = self.encryption.agent_schluessel("browser-profil", "abc12345")
        self.assertEqual(a, self.encryption.agent_schluessel("browser-profil", "abc12345"))
        Fernet(a.encode())  # wirft nicht
        self.assertEqual(32, len(base64.urlsafe_b64decode(a)))

    def test_je_agent_und_zweck_verschieden_und_nie_der_stamm(self):
        werte = {
            self.encryption.agent_schluessel(zweck, agent)
            for zweck in ("browser-profil", "browser-zugang")
            for agent in ("abc12345", "def67890")
        }
        self.assertEqual(4, len(werte))
        self.assertNotIn(self.stamm, werte)


class Hostnamen(unittest.TestCase):
    def test_hostnamen_werden_zugelassen_nicht_nur_gesperrt(self):
        from app.core.browser_domains import UngueltigeDomain, bereinigen

        self.assertEqual(["xn--mnchen-3ya.de"], bereinigen(["xn--mnchen-3ya.de"]))
        for wert in ("exämple.com", "a..b.com", "-a.com", "a_b.com", "a.com:8080"):
            with self.subTest(wert=wert), self.assertRaises(UngueltigeDomain):
                bereinigen([wert])


class FreigabelisteNurBesitzer(unittest.IsolatedAsyncioTestCase):
    """Wer den Agenten nur freigegeben bekommen hat, verschiebt seine Grenze nicht."""

    async def _aendern(self, rolle, nutzer_id, besitzer_id):
        from fastapi import HTTPException

        from app.api import agents as api
        from app.models.user import UserRole

        nutzer = mock.Mock(id=nutzer_id, role=getattr(UserRole, rolle))
        db = mock.AsyncMock()
        db.scalar.return_value = besitzer_id
        agent = mock.Mock(config={}, container_id=None)
        manager = mock.Mock()
        manager._get_agent = mock.AsyncMock(return_value=agent)
        with mock.patch.object(api, "_check_owner", mock.AsyncMock()), \
                mock.patch("sqlalchemy.orm.attributes.flag_modified"):
            try:
                await api.update_agent_browser_domains(
                    "abc12345", api.BrowserDomainsUpdate(allowed_domains=["example.com"]),
                    user=nutzer, db=db, manager=manager)
            except HTTPException as e:
                return e.status_code, agent.config
        return 200, agent.config

    async def test_freigegebener_nutzer_wird_abgewiesen(self):
        status, config = await self._aendern("MEMBER", "fremd", "besitzer")
        self.assertEqual(403, status)
        self.assertEqual({}, config)

    async def test_besitzer_und_admin_duerfen(self):
        self.assertEqual(200, (await self._aendern("MEMBER", "besitzer", "besitzer"))[0])
        self.assertEqual(200, (await self._aendern("ADMIN", "admin", "besitzer"))[0])


class NurDerBesitzerSiehtDenBrowser(unittest.IsolatedAsyncioTestCase):
    """Bildstrom und Pruefprotokoll zeigen die angemeldeten Sitzungen des Besitzers."""

    async def _pruefen(self, nutzer_id, rolle, besitzer_id):
        from fastapi import HTTPException

        from app.dependencies import require_agent_owner
        from app.models.user import UserRole

        db = mock.AsyncMock()
        db.scalar.return_value = besitzer_id
        nutzer = mock.Mock(id=nutzer_id, role=getattr(UserRole, rolle))
        try:
            await require_agent_owner("abc12345", nutzer, db)
        except HTTPException as e:
            return e.status_code
        return 200

    async def test_besitzer_ja_alle_anderen_nein(self):
        self.assertEqual(200, await self._pruefen("a", "MEMBER", "a"))
        for rolle in ("ADMIN", "MANAGER", "MEMBER"):
            with self.subTest(rolle=rolle):
                self.assertEqual(404, await self._pruefen("b", rolle, "a"))
        self.assertEqual(404, await self._pruefen("b", "ADMIN", None))


class BrowserVariablenNichtUeberKeys(unittest.TestCase):
    def test_fremder_key_darf_browser_variablen_nicht_setzen(self):
        from app.core.secret_zugriff import variable_reserviert

        for name in ("BROWSER_ZUGANG", "BROWSER_PROFIL_KEY", "BROWSER_PROFIL_ARCHIV",
                     "BROWSER_ALLOWED_DOMAINS", "COMPUTER_USE_BROWSER"):
            with self.subTest(name=name):
                self.assertTrue(variable_reserviert(name))


if __name__ == "__main__":
    unittest.main()
