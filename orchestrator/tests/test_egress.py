"""Egress-Proxy: Keys verlassen die Plattform erst am Ausgang (core/egress.py, api/egress.py).

Geprueft wird die ganze Kette ausser dem laufenden mitmproxy (den prueft der
Live-Test auf der Anlage):

* Platzhalter: signiert, nicht fuer fremde Keys erfindbar.
* Erlaubte Ziele: Pruefung beim Speichern, Abgleich (auch Unterdomaenen-Muster).
* Einspielen: mit Proxy + Zielen nur der Platzhalter, sonst wie bisher.
* Aufloesen: nur fuer den eigenen, noch nutzbaren Key und nur am erlaubten Ziel —
  alle oder keiner, Verweigerung im Protokoll.
* Proxy-Logik (egress/egress_logik.py): Anmeldung, interne Ziele gesperrt,
  Basic-Anmeldung von git, Rueckmaskierung, Zwischenspeicher mit Notbetrieb.
"""
import base64
import socket
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api import egress as api_egress
from app.config import settings
from app.core import egress
from app.models.agent import Agent
from app.models.agent_secret import AgentSecretAssignment
from app.models.user import User, UserRole
from tests.test_secrets_gehoeren_ihrem_ersteller import ADMIN, MITGLIED, SecretsTestBasis

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "egress"))
import egress_logik as logik  # noqa: E402

TOKEN = "p" * 64


def _an():
    return [patch.object(settings, "egress_proxy_enabled", True),
            patch.object(settings, "egress_proxy_token", TOKEN)]


class AnTestFall(unittest.TestCase):
    def setUp(self):
        self.p = _an()
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()


class PlatzhalterTests(unittest.TestCase):
    def test_rundweg(self):
        p = egress.platzhalter(42)
        self.assertRegex(p, r"^aie-geheim-42-[0-9a-f]{12}$")
        self.assertEqual(egress.secret_id_aus(p), 42)

    def test_fuer_fremden_key_nicht_erfindbar(self):
        # Signatur von Key 42 an Key 43 gehaengt → kein Treffer
        sig = egress.platzhalter(42).rsplit("-", 1)[1]
        self.assertIsNone(egress.secret_id_aus(f"aie-geheim-43-{sig}"))
        self.assertIsNone(egress.secret_id_aus("aie-geheim-42-000000000000"))
        self.assertIsNone(egress.secret_id_aus("irgendwas"))
        self.assertIsNone(egress.secret_id_aus(egress.platzhalter(42) + "x"))

    def test_proxy_erkennt_das_format(self):
        self.assertEqual(logik.platzhalter_in(f"Bearer {egress.platzhalter(7)}"), {egress.platzhalter(7)})
        g = egress.platzhalter(3, egress.ANBINDUNG)
        self.assertEqual(logik.platzhalter_in(f"token {g}"), {g})

    def test_arten_getrennt(self):
        g = egress.platzhalter(3, egress.ANBINDUNG)
        self.assertRegex(g, r"^aie-geheim-g3-[0-9a-f]{12}$")
        self.assertEqual(egress.platzhalter_aus(g), ("g", 3))
        self.assertIsNone(egress.secret_id_aus(g), "Anbindung ist kein Key der Schluesselverwaltung")
        # Signatur einer Art gilt nicht fuer die andere
        self.assertIsNone(egress.platzhalter_aus("aie-geheim-g3-" + egress.platzhalter(3).rsplit("-", 1)[1]))
        # Bestehende Key-Platzhalter bleiben gleich (laufende Agenten tragen sie schon)
        import hashlib, hmac as _h
        alt = _h.new(settings.api_secret_key.encode(), b"egress-platzhalter:v1:3", hashlib.sha256).hexdigest()[:12]
        self.assertEqual(egress.platzhalter(3), f"aie-geheim-3-{alt}")

    def test_ziele_der_git_anbieter(self):
        from app.core.githost.registry import get_git_host_provider
        self.assertEqual(get_git_host_provider("github").egress_ziele(),
                         ["api.github.com", "github.com", "uploads.github.com"])
        self.assertEqual(get_git_host_provider("github", "https://GHE.example.com:8443").egress_ziele(),
                         ["ghe.example.com"])
        self.assertEqual(get_git_host_provider("forgejo", "https://git.example.com/").egress_ziele(),
                         ["git.example.com"])


class HostsTests(unittest.TestCase):
    def test_bereinigen(self):
        self.assertEqual(egress.hosts_pruefen(["API.GitHub.com.", "api.github.com", " Uploads.GitHub.com "]),
                         ["api.github.com", "uploads.github.com"])
        self.assertEqual(egress.hosts_pruefen("a.example.com, b.example.com"), ["a.example.com", "b.example.com"])
        self.assertIsNone(egress.hosts_pruefen([]))
        self.assertIsNone(egress.hosts_pruefen(None))

    def test_ungueltig(self):
        for falsch in ("https://api.github.com", "api.github.com/repos", "api.github.com:443", "localhost",
                       "*.com", "*", "a..b.com", "-a.example.com", "exa mple.com",
                       # Muster gar nicht: Unter *.github.io legt jeder eine Adresse an
                       "*.example.com", "*.github.io"):
            with self.subTest(falsch), self.assertRaises(ValueError):
                egress.hosts_pruefen([falsch])

    def test_hoechstens_zwanzig(self):
        with self.assertRaises(ValueError):
            egress.hosts_pruefen([f"h{i}.example.com" for i in range(21)])

    def test_abgleich(self):
        m = ["api.github.com", "github.com"]
        self.assertTrue(egress.host_passt("api.github.com", m))
        self.assertTrue(egress.host_passt("API.GITHUB.COM.", m))
        for nein in ("uploads.github.com", "evilgithub.com", "api.github.com.evil.com",
                     "xapi.github.com", ""):
            with self.subTest(nein):
                self.assertFalse(egress.host_passt(nein, m))
        # Proxy und Orchestrator gleichen gleich ab
        for h in ("api.github.com", "github.com", "uploads.github.com", "evilgithub.com"):
            self.assertEqual(logik.host_passt(h, m), egress.host_passt(h, m))


class SchalterTests(unittest.TestCase):
    def test_ohne_dienstschluessel_aus(self):
        with patch.object(settings, "egress_proxy_enabled", True), patch.object(settings, "egress_proxy_token", "kurz"):
            self.assertFalse(egress.aktiv())
            self.assertFalse(egress.ist_proxy_anfrage("Bearer kurz"))
        with patch.object(settings, "egress_proxy_enabled", False), patch.object(settings, "egress_proxy_token", TOKEN):
            self.assertFalse(egress.aktiv())
            self.assertFalse(egress.ist_proxy_anfrage(f"Bearer {TOKEN}"))

    def test_proxy_anfrage(self):
        with patch.object(settings, "egress_proxy_enabled", True), patch.object(settings, "egress_proxy_token", TOKEN):
            self.assertTrue(egress.ist_proxy_anfrage(f"Bearer {TOKEN}"))
            self.assertFalse(egress.ist_proxy_anfrage("Bearer " + "x" * 64))
            self.assertFalse(egress.ist_proxy_anfrage(""))


class AktualisierenHinweisTests(unittest.TestCase):
    """Nach dem Ein- oder Ausschalten zeigen Agenten "Aktualisieren", bis sie neu entstanden sind."""

    def test_veraltet(self):
        with patch.object(settings, "egress_proxy_enabled", True), patch.object(settings, "egress_proxy_token", TOKEN):
            self.assertTrue(egress.veraltet({}))                   # Agent von vor dem Proxy
            self.assertTrue(egress.veraltet({"egress": False}))
            self.assertFalse(egress.veraltet({"egress": True}))
        with patch.object(settings, "egress_proxy_enabled", False):
            self.assertFalse(egress.veraltet({}))
            self.assertFalse(egress.veraltet(None))
            self.assertTrue(egress.veraltet({"egress": True}))     # Proxy wieder aus


class UmgebungTests(unittest.IsolatedAsyncioTestCase):
    async def test_aus_ist_leer(self):
        with patch.object(settings, "egress_proxy_enabled", False):
            self.assertEqual(await egress.agent_umgebung("a1"), {})

    async def test_an(self):
        from app.dependencies import make_agent_token
        with patch.object(settings, "egress_proxy_enabled", True), \
                patch.object(settings, "egress_proxy_token", TOKEN), \
                patch.object(egress, "ca_zertifikat", AsyncMock(return_value="-----BEGIN CERTIFICATE-----\nX\n")):
            env = await egress.agent_umgebung("a1")
        self.assertEqual(env["HTTPS_PROXY"],
                         f"http://a1:{make_agent_token('a1')}@ai-employee-egress-proxy:8080")
        self.assertEqual(env["https_proxy"], env["HTTPS_PROXY"])
        self.assertNotIn("HTTP_PROXY", env, "Klartext-HTTP (eigene Apps) bleibt direkt")
        self.assertIn("ai-employee-orchestrator", env["NO_PROXY"])
        self.assertTrue(env["AIE_EGRESS_CA"].startswith("-----BEGIN CERTIFICATE-----"))
        self.assertNotIn(TOKEN, "".join(env.values()), "Der Dienst-Schluessel geht nie in den Agenten")

    async def test_ohne_ca_kein_start(self):
        with patch.object(settings, "egress_proxy_enabled", True), \
                patch.object(settings, "egress_proxy_token", TOKEN), \
                patch.object(egress, "ca_zertifikat", AsyncMock(side_effect=RuntimeError("weg"))):
            with self.assertRaises(RuntimeError):
                await egress.agent_umgebung("a1")


class EgressDatenTests(SecretsTestBasis):
    """Einspielen und Aufloesen gegen eine echte (SQLite-)Datenbank."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app.models.oauth_integration import OAuthIntegration
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[Agent.__table__, OAuthIntegration.__table__])
        self.db.add(User(id="m1", email="m1@example.invalid", name="M1", role=UserRole.MEMBER))
        self.db.add(User(id="m2", email="m2@example.invalid", name="M2", role=UserRole.MEMBER))
        self.db.add(Agent(id="a1", name="A1", user_id="m1"))
        self.db.add(Agent(id="a2", name="A2", user_id="m2"))
        await self.db.commit()
        from app.api import secrets as api
        body = api.SecretCreate(name="GH", key_name="GITHUB_TOKEN", value="ghp_echt_1234567890",
                                erlaubte_hosts=["api.github.com", "github.com"])
        self.gh = await api.create_secret(body, user=MITGLIED, db=self.db)
        body = api.SecretCreate(name="Alt", key_name="ALT_KEY", value="alt_wert_123456")
        self.alt = await api.create_secret(body, user=MITGLIED, db=self.db)
        for sid in (self.gh["id"], self.alt["id"]):
            self.db.add(AgentSecretAssignment(agent_id="a1", secret_id=sid))
        await self.db.commit()
        self.an = _an()
        for p in self.an:
            p.start()
        self.dec = patch("app.api.egress.decrypt_token", lambda w: w[4:])
        self.dec.start()
        self.audit = patch("app.api.egress.protokolliere", AsyncMock())
        self.protokoll = self.audit.start()

    async def asyncTearDown(self):
        for p in self.an + [self.dec, self.audit]:
            p.stop()
        await super().asyncTearDown()

    def _anm(self, agent="a1"):
        from app.dependencies import make_agent_token
        return {"agent_id": agent, "agent_token": make_agent_token(agent)}

    async def _aufloesen(self, host, *platz, agent="a1"):
        body = api_egress.Aufloesen(**self._anm(agent), host=host, platzhalter=list(platz))
        return await api_egress.aufloesen(body, db=self.db)

    # --- Einspielen --------------------------------------------------------------
    async def test_einspielen_mit_proxy(self):
        from app.core.agent_manager import AgentManager
        mgr = AgentManager.__new__(AgentManager)
        mgr.db = self.db
        with patch("app.core.agent_manager.decrypt_token", lambda w: w[4:]):
            env = await mgr._get_secrets_env("a1")
        self.assertEqual(env["GITHUB_TOKEN"], egress.platzhalter(self.gh["id"]))
        self.assertEqual(env["GH_TOKEN"], env["GITHUB_TOKEN"])
        self.assertEqual(env["ALT_KEY"], "alt_wert_123456", "ohne Ziele wie bisher")
        self.assertNotIn("ghp_echt_1234567890", "".join(env.values()))

    async def test_plattform_variablen_nie(self):
        from app.api import secrets as api
        from app.core.agent_manager import AgentManager
        for name in ("AIE_EGRESS_CA", "ENTRYPOINT_MODULE_DIR", "entrypoint_ca_dir"):
            with self.subTest(name), self.assertRaises(HTTPException) as e:
                await api.create_secret(api.SecretCreate(name=name, key_name=name, value="x"),
                                        user=MITGLIED, db=self.db)
            self.assertEqual(e.exception.status_code, 422)
        # Auch ein Altbestand (vor der Sperre angelegt) kommt nicht in den Agenten
        from app.models.agent_secret import AgentSecret
        alt = AgentSecret(name="x", key_name="ENTRYPOINT_CA_DIR", value_encrypted="enc:/etc", owner_id="m1")
        self.db.add(alt)
        await self.db.commit()
        self.db.add(AgentSecretAssignment(agent_id="a1", secret_id=alt.id))
        await self.db.commit()
        mgr = AgentManager.__new__(AgentManager)
        mgr.db = self.db
        with patch("app.core.agent_manager.decrypt_token", lambda w: w[4:]):
            self.assertNotIn("ENTRYPOINT_CA_DIR", await mgr._get_secrets_env("a1"))

    async def test_einspielen_ohne_proxy_wie_bisher(self):
        from app.core.agent_manager import AgentManager
        mgr = AgentManager.__new__(AgentManager)
        mgr.db = self.db
        with patch.object(settings, "egress_proxy_enabled", False), \
                patch("app.core.agent_manager.decrypt_token", lambda w: w[4:]):
            env = await mgr._get_secrets_env("a1")
        self.assertEqual(env["GITHUB_TOKEN"], "ghp_echt_1234567890")

    # --- Verbindung ----------------------------------------------------------------
    async def test_verbindung_liefert_ziele(self):
        r = await api_egress.verbindung(api_egress.Verbindung(**self._anm()), db=self.db)
        self.assertEqual(r, {"hosts": ["api.github.com", "github.com"]})
        r = await api_egress.verbindung(api_egress.Verbindung(**self._anm("a2")), db=self.db)
        self.assertEqual(r, {"hosts": []})

    async def test_verbindung_falsches_token(self):
        for body in ({"agent_id": "a1", "agent_token": "falsch"},
                     {**self._anm("zz"), "agent_id": "zz"}):          # gueltiges Token, Agent unbekannt
            with self.subTest(body["agent_id"]), self.assertRaises(HTTPException) as e:
                await api_egress.verbindung(api_egress.Verbindung(**body), db=self.db)
            self.assertEqual(e.exception.status_code, 401)

    def test_nur_mit_dienstschluessel(self):
        for kopf in ("", "Bearer falsch", f"Bearer {TOKEN[:-1]}"):
            with self.subTest(kopf), self.assertRaises(HTTPException) as e:
                api_egress._nur_proxy(SimpleNamespace(headers={"Authorization": kopf}))
            self.assertEqual(e.exception.status_code, 403)
        api_egress._nur_proxy(SimpleNamespace(headers={"Authorization": f"Bearer {TOKEN}"}))

    # --- Aufloesen -----------------------------------------------------------------
    async def test_erlaubtes_ziel(self):
        p = egress.platzhalter(self.gh["id"])
        self.assertEqual(await self._aufloesen("api.github.com", p), {"werte": {p: "ghp_echt_1234567890"}})
        self.protokoll.assert_not_awaited()

    async def test_fremdes_ziel_verweigert_und_protokolliert(self):
        r = await self._aufloesen("evil.example.com", egress.platzhalter(self.gh["id"]))
        self.assertEqual(r.status_code, 403)
        self.assertNotIn(b"ghp_echt", r.body)
        self.protokoll.assert_awaited_once()
        self.assertEqual(self.protokoll.await_args.args[1], "egress_key_verweigert")

    async def test_key_ohne_ziele_nie(self):
        r = await self._aufloesen("api.github.com", egress.platzhalter(self.alt["id"]))
        self.assertEqual(r.status_code, 403)

    async def test_key_eines_anderen_agenten(self):
        r = await self._aufloesen("api.github.com", egress.platzhalter(self.gh["id"]), agent="a2")
        self.assertEqual(r.status_code, 403)

    async def test_alle_oder_keiner(self):
        gut, schlecht = egress.platzhalter(self.gh["id"]), egress.platzhalter(self.alt["id"])
        r = await self._aufloesen("api.github.com", gut, schlecht)
        self.assertEqual(r.status_code, 403)
        self.assertNotIn(b"ghp_echt", r.body)

    async def test_deaktivierter_key(self):
        from app.models.agent_secret import AgentSecret
        (await self.db.get(AgentSecret, self.gh["id"])).is_active = False
        await self.db.commit()
        r = await self._aufloesen("api.github.com", egress.platzhalter(self.gh["id"]))
        self.assertEqual(r.status_code, 403)

    async def test_entzogener_key(self):
        # Besitzer darf den Key nicht mehr nutzen (z. B. Freigabe entzogen) → auch am Ausgang weg
        with patch("app.api.egress.nutzbare_secrets_des_besitzers", AsyncMock(return_value=set())):
            r = await self._aufloesen("api.github.com", egress.platzhalter(self.gh["id"]))
        self.assertEqual(r.status_code, 403)

    async def test_gefaelschter_platzhalter(self):
        r = await self._aufloesen("api.github.com", f"aie-geheim-{self.gh['id']}-000000000000")
        self.assertEqual(r.status_code, 403)

    # --- Git-Anbindung der Plattform ---------------------------------------------------
    async def _anbindung(self):
        from app.models.oauth_integration import OAuthIntegration, OAuthProvider
        i = OAuthIntegration(provider=OAuthProvider.GITHUB, user_id=None, access_token_encrypted="enc:ghp_plattform_123")
        self.db.add(i)
        (await self.db.get(Agent, "a1")).config = {"integrations": ["github"]}
        await self.db.commit()
        return i

    async def test_anbindung_ueber_den_proxy(self):
        i = await self._anbindung()
        g = egress.platzhalter(i.id, egress.ANBINDUNG)
        r = await api_egress.verbindung(api_egress.Verbindung(**self._anm()), db=self.db)
        self.assertIn("uploads.github.com", r["hosts"])
        self.assertEqual(await self._aufloesen("api.github.com", g), {"werte": {g: "ghp_plattform_123"}})
        self.assertEqual((await self._aufloesen("evil.example.com", g)).status_code, 403)
        # a2 hat die Anbindung nicht zugewiesen
        self.assertEqual((await self._aufloesen("api.github.com", g, agent="a2")).status_code, 403)
        # Die Signatur stimmt, aber als Key-ID gelesen gehoert sie zu nichts
        self.assertEqual((await self._aufloesen("api.github.com", egress.platzhalter(i.id)) ).status_code, 403)

    async def test_anbindung_einspielen(self):
        from app.core.agent_manager import AgentManager
        i = await self._anbindung()
        mgr = AgentManager.__new__(AgentManager)
        mgr.db = self.db
        with patch("app.core.agent_manager.decrypt_token", lambda w: w[4:]):
            env = await mgr._get_integration_env(["github"])
            self.assertEqual(env["GITHUB_TOKEN"], egress.platzhalter(i.id, egress.ANBINDUNG))
            with patch.object(settings, "egress_proxy_enabled", False):
                self.assertEqual((await mgr._get_integration_env(["github"]))["GITHUB_TOKEN"], "ghp_plattform_123")

    async def test_anbindung_im_sicherheitsstatus(self):
        from app.core.sicherheitsstatus import _schluessel
        await self._anbindung()
        with patch.object(settings, "egress_proxy_enabled", False):
            (p,) = await _schluessel(self.db)
        self.assertIn("Git-Anbindung", p["text"])

    # --- Schluesselverwaltung --------------------------------------------------------
    async def test_ungueltige_ziele_422(self):
        from app.api import secrets as api
        body = api.SecretCreate(name="X", key_name="X", value="v", erlaubte_hosts=["https://x.example.com/"])
        with self.assertRaises(HTTPException) as e:
            await api.create_secret(body, user=MITGLIED, db=self.db)
        self.assertEqual(e.exception.status_code, 422)

    async def test_ziele_aendern_und_neu_aufsetzen(self):
        from app.api import secrets as api
        refresh = api._refresh_agents_for_secret
        refresh.reset_mock()
        # Klartext → Platzhalter: Agent muss neu aufgesetzt werden
        r = await api.update_secret(self.alt["id"], api.SecretUpdate(erlaubte_hosts=["api.example.com"]),
                                    user=MITGLIED, db=self.db, manager=self.manager)
        self.assertEqual((r["erlaubte_hosts"], r["ueber_proxy"]), (["api.example.com"], True))
        self.assertEqual(refresh.await_count, 1)
        # Neuer Wert hinter dem Proxy: gilt sofort, kein Neuaufsetzen
        await api.update_secret(self.alt["id"], api.SecretUpdate(value="neu_123456789"),
                                user=MITGLIED, db=self.db, manager=self.manager)
        self.assertEqual(refresh.await_count, 1)
        # Aelterer Client schickt das unveraenderte Aktiv-Haekchen mit: kein Neuaufsetzen
        await api.update_secret(self.alt["id"], api.SecretUpdate(is_active=True, description="x"),
                                user=MITGLIED, db=self.db, manager=self.manager)
        self.assertEqual(refresh.await_count, 1)
        # Name aendern laesst die Ziele stehen
        r = await api.update_secret(self.alt["id"], api.SecretUpdate(name="Anders"),
                                    user=MITGLIED, db=self.db, manager=self.manager)
        self.assertEqual(r["erlaubte_hosts"], ["api.example.com"])
        # Ziele leeren → wieder Klartext → neu aufsetzen
        r = await api.update_secret(self.alt["id"], api.SecretUpdate(erlaubte_hosts=[]),
                                    user=MITGLIED, db=self.db, manager=self.manager)
        self.assertEqual((r["erlaubte_hosts"], r["ueber_proxy"]), ([], False))
        self.assertEqual(refresh.await_count, 2)

    # --- Sicherheitsstatus --------------------------------------------------------------
    async def test_sicherheitsstatus(self):
        from app.core.sicherheitsstatus import _schluessel
        (p,) = await _schluessel(self.db)
        self.assertEqual((p["stand"], "ALT_KEY" in p["text"], "GITHUB_TOKEN" in p["text"]), ("warnung", True, False))
        with patch.object(settings, "egress_proxy_enabled", False):
            (p,) = await _schluessel(self.db)
        self.assertEqual(p["stand"], "warnung")
        self.assertIn("GITHUB_TOKEN", p["text"])
        with patch.object(settings, "egress_proxy_token", ""):
            (p,) = await _schluessel(self.db)
        self.assertEqual(p["stand"], "kritisch")
        from app.models.agent_secret import AgentSecret
        (await self.db.get(AgentSecret, self.alt["id"])).erlaubte_hosts = ["api.example.com"]
        await self.db.commit()
        # Alle Keys haben Ziele, aber a1 entstand vor dem Einschalten: kein "ok"
        (p,) = await _schluessel(self.db)
        self.assertEqual(p["stand"], "warnung")
        self.assertIn("vor dem Einschalten", p["text"])
        (await self.db.get(Agent, "a1")).config = {"egress": True}
        await self.db.commit()
        (p,) = await _schluessel(self.db)
        self.assertEqual(p["stand"], "ok")


class ProxyLogikTests(unittest.TestCase):
    def test_anmeldung(self):
        kopf = "Basic " + base64.b64encode(b"a1:tok").decode()
        self.assertEqual(logik.anmeldung(kopf), ("a1", "tok"))
        for falsch in (None, "", "Bearer x", "Basic ???", "Basic " + base64.b64encode(b"ohne-doppelpunkt").decode(),
                       "Basic " + base64.b64encode(b":tok").decode()):
            with self.subTest(falsch):
                self.assertIsNone(logik.anmeldung(falsch))

    def test_interne_ziele_gesperrt(self):
        def aufl(*ips):
            return lambda host, port, type=None: [(socket.AF_INET, type, 6, "", (ip, port)) for ip in ips]
        for ip in ("10.0.0.5", "192.168.178.1", "172.17.0.1", "127.0.0.1", "169.254.169.254", "100.64.0.1"):
            with self.subTest(ip), self.assertRaises(logik.Abgelehnt) as e:
                logik.ziel_adresse("x.example.com", 443, aufloesen=aufl(ip))
            self.assertEqual(e.exception.status, 403)
        # Ein interner Eintrag unter mehreren reicht zum Sperren (kein Ausweichen per DNS)
        with self.assertRaises(logik.Abgelehnt):
            logik.ziel_adresse("x.example.com", 443, aufloesen=aufl("93.184.215.14", "10.0.0.1"))
        self.assertEqual(logik.ziel_adresse("x.example.com", 443, aufloesen=aufl("93.184.215.14")), "93.184.215.14")

    def test_basic_von_git(self):
        p = egress.platzhalter(5)
        kopf = "Basic " + base64.b64encode(f"x-access-token:{p}".encode()).decode()
        self.assertEqual(logik.kopf_platzhalter("Authorization", kopf), {p})
        neu = logik.kopf_ersetzen("Authorization", kopf, {p: "ghp_echt"})
        self.assertEqual(base64.b64decode(neu[6:]).decode(), "x-access-token:ghp_echt")
        self.assertEqual(logik.kopf_ersetzen("Authorization", f"token {p}", {p: "ghp_echt"}), "token ghp_echt")

    def test_nur_an_anmeldestellen(self):
        p = egress.platzhalter(5)
        f = logik.platzhalter_ausserhalb_der_koepfe
        self.assertEqual(f(f"/maps/api?key={p}&q=Berlin", "", None), {p})
        self.assertEqual(f("/x", "application/x-www-form-urlencoded",
                           f"grant_type=client_credentials&client_secret={p}"), {p})
        self.assertEqual(f("/x", "application/json", '{"a": 1}'), set())
        for pfad, art, koerper in (
            (f"/repos/o/r/contents/{p}.txt", "", None),          # Dateiname beim Ziel
            (f"/x?q={p}", "", None),                             # beliebiger Parameter (z. B. Suche, Kommentar)
            (f"/x?key=Bearer%20{p}", "", None),                  # nicht als ganzer Wert
            ("/gists", "application/json", f'{{"content": "{p}"}}'),
            ("/x", "application/x-www-form-urlencoded", f"body={p}"),
            # JSON, das sich als Formular ausgibt: & teilt es scheinbar in Parameter
            ("/x", "application/x-www-form-urlencoded", f'{{"body":"a&token={p}&"}}'),
            ("/x", ["application/x-www-form-urlencoded", "application/json"], f"token={p}"),
            ("/x", "application/x-www-form-urlencoded; x=application/json", f"password={p}"),
            ("/x", "text/plain", p),
        ):
            with self.subTest(pfad=pfad, koerper=koerper), self.assertRaises(logik.Abgelehnt) as e:
                f(pfad, art, koerper)
            self.assertEqual(e.exception.status, 403)

    def test_ersetzen_nur_im_geprueften_wert(self):
        p = egress.platzhalter(5)
        w = {p: "echt/+="}
        self.assertEqual(logik.pfad_ersetzen(f"/a?q=1&key={p}", w), "/a?q=1&key=echt%2F%2B%3D")
        self.assertEqual(logik.pfad_ersetzen("/a?q=1", w), "/a?q=1")
        self.assertEqual(logik.parameter_ersetzen(f"grant_type=x&client_secret={p}", w),
                         "grant_type=x&client_secret=echt%2F%2B%3D")

    def test_rueckmaskieren(self):
        p = egress.platzhalter(5)
        self.assertEqual(logik.zurueck_maskieren('{"auth":"Bearer ghp_echt_123"}', {p: "ghp_echt_123"}),
                         f'{{"auth":"Bearer {p}"}}')
        self.assertEqual(logik.zurueck_maskieren("abc", {p: "abc"}), "abc", "kurze Werte nicht blind ersetzen")
        # Kodierte Spiegelungen (Fehlermeldungen, PHP-JSON, Weiterleitungen)
        w = "ab/cd+ef=gh_1234567"
        for kodiert in ("ab\\/cd+ef=gh_1234567", "ab%2Fcd%2Bef%3Dgh_1234567",
                        base64.b64encode(w.encode()).decode()):
            with self.subTest(kodiert):
                self.assertEqual(logik.zurueck_maskieren(f"x {kodiert} y", {p: w}), f"x {p} y")
        self.assertEqual(logik.zurueck_maskieren_bytes(b"\x00" + w.encode() + b"\xff", {p: w}),
                         b"\x00" + p.encode() + b"\xff")

    def test_host_werte(self):
        z = "api.github.com"
        self.assertTrue(logik.host_werte_passen(["api.github.com"], z))
        self.assertTrue(logik.host_werte_passen(["API.GitHub.com:443", "api.github.com."], z))
        for falsch in ([], [""], ["evil.example.com"], ["api.github.com", "evil.example.com"],
                       ["api.github.com:443:1"], ["[::1]:443"]):
            with self.subTest(falsch):
                self.assertFalse(logik.host_werte_passen(falsch, z))

    def test_entpacken_begrenzt(self):
        import gzip
        import zlib
        text = b"hallo " * 100
        self.assertEqual(logik.entpacken_begrenzt(gzip.compress(text), "gzip"), text)
        self.assertEqual(logik.entpacken_begrenzt(zlib.compress(text), "deflate"), text)
        self.assertEqual(logik.entpacken_begrenzt(text, "identity"), text)
        bombe = gzip.compress(b"\0" * (5 * 1024 * 1024))         # 5 MB aus ~5 KB
        with self.assertRaises(logik.Abgelehnt):
            logik.entpacken_begrenzt(bombe, "gzip", grenze=1024 * 1024)
        for k in ("br", "zstd", "compress"):
            with self.subTest(k), self.assertRaises(logik.Abgelehnt):
                logik.entpacken_begrenzt(b"x", k)

    def test_maske_je_agent(self):
        uhr = [0.0]
        m = logik.Maske(jetzt=lambda: uhr[0])
        m.merken("a1", {"p1": "wert-1234567"})
        self.assertEqual(m.werte("a1"), {"p1": "wert-1234567"})
        self.assertEqual(m.werte("a2"), {}, "fremde Agenten nicht")
        uhr[0] += logik.MASKE_DAUER
        self.assertEqual(m.werte("a1"), {})

    def test_uebersetzte_adressen_gesperrt(self):
        def aufl(ip):
            return lambda host, port, type=None: [(socket.AF_INET6, type, 6, "", (ip, port, 0, 0))]
        for ip in ("64:ff9b::a00:1", "2002:c0a8:101::1"):
            with self.subTest(ip), self.assertRaises(logik.Abgelehnt):
                logik.ziel_adresse("x.example.com", 443, aufloesen=aufl(ip))


class OrchestratorKlientTests(unittest.TestCase):
    def setUp(self):
        self.uhr = [1000.0]
        self.antworten = []
        self.aufrufe = []

        def senden(pfad, daten):
            self.aufrufe.append(pfad)
            a = self.antworten.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        self.o = logik.Orchestrator("http://o", "t", jetzt=lambda: self.uhr[0], senden=senden)

    def test_anmeldung_zwischengespeichert_und_notbetrieb(self):
        self.antworten = [(200, {"hosts": ["api.github.com"]})]
        self.assertEqual(self.o.hosts(("a1", "t")), ["api.github.com"])
        self.uhr[0] += 30
        self.assertEqual(self.o.hosts(("a1", "t")), ["api.github.com"])
        self.assertEqual(len(self.aufrufe), 1)
        # Orchestrator startet neu: die bestaetigte Anmeldung gilt weiter
        self.uhr[0] += 300
        self.antworten = [logik.OrchestratorFehler("weg")]
        self.assertEqual(self.o.hosts(("a1", "t")), ["api.github.com"])
        # ... aber nicht ewig
        self.uhr[0] += logik.ANMELDUNG_NOTFALL
        self.antworten = [logik.OrchestratorFehler("weg")]
        with self.assertRaises(logik.OrchestratorFehler):
            self.o.hosts(("a1", "t"))
        # Unbekannter Agent ohne frueheren Erfolg: Fehler, kein Durchwinken
        self.antworten = [logik.OrchestratorFehler("weg")]
        with self.assertRaises(logik.OrchestratorFehler):
            self.o.hosts(("a9", "t"))

    def test_falsche_anmeldung(self):
        self.antworten = [(401, {})]
        self.assertIsNone(self.o.hosts(("a1", "falsch")))

    def test_werte(self):
        p = egress.platzhalter(5)
        self.antworten = [(200, {"werte": {p: "echt"}})]
        self.assertEqual(self.o.werte(("a1", "t"), "api.github.com", {p}), {p: "echt"})
        self.assertEqual(self.o.werte(("a1", "t"), "api.github.com", {p}), {p: "echt"})
        self.assertEqual(len(self.aufrufe), 1, "kurz zwischengespeichert")
        # anderes Ziel = eigene Frage
        self.antworten = [(403, {})]
        with self.assertRaises(logik.Abgelehnt) as e:
            self.o.werte(("a1", "t"), "evil.example.com", {p})
        self.assertEqual(e.exception.status, 403)
        # Orchestrator startet neu: bestaetigter Wert gilt weiter ...
        self.uhr[0] += 120
        self.antworten = [logik.OrchestratorFehler("weg")]
        self.assertEqual(self.o.werte(("a1", "t"), "api.github.com", {p}), {p: "echt"})
        # ... eine Ablehnung aber sofort, und danach auch im Notbetrieb nicht mehr
        self.antworten = [(403, {})]
        with self.assertRaises(logik.Abgelehnt):
            self.o.werte(("a1", "t"), "api.github.com", {p})
        self.antworten = [logik.OrchestratorFehler("weg")]
        with self.assertRaises(logik.OrchestratorFehler):
            self.o.werte(("a1", "t"), "api.github.com", {p})
        # ... und nicht ewig
        self.antworten = [(200, {"werte": {p: "echt"}})]
        self.o.werte(("a1", "t"), "api.github.com", {p})
        self.uhr[0] += logik.WERTE_NOTFALL
        self.antworten = [logik.OrchestratorFehler("weg")]
        with self.assertRaises(logik.OrchestratorFehler):
            self.o.werte(("a1", "t"), "api.github.com", {p})
        # Ablehnung des Dienst-Schluessels (403) ueberbrueckt die Anmeldung nicht
        self.antworten = [(200, {"hosts": []})]
        self.o.hosts(("a7", "t"))
        self.uhr[0] += logik.ANMELDUNG_FRISCH
        self.antworten = [(403, {})]
        with self.assertRaises(logik.OrchestratorFehler):
            self.o.hosts(("a7", "t"))
        # unvollstaendige Antwort wird nicht halb eingesetzt
        q = egress.platzhalter(6)
        self.antworten = [(200, {"werte": {p: "echt"}})]
        with self.assertRaises(logik.Abgelehnt):
            self.o.werte(("a1", "t"), "api.github.com", {p, q})


class DienstSchluesselTests(unittest.TestCase):
    """scripts/lib/sicherung.sh: egress_token_sicherstellen (setup.sh, update.sh)."""

    def _lauf(self, inhalt):
        import subprocess
        import tempfile
        lib = Path(__file__).resolve().parents[2] / "scripts" / "lib" / "sicherung.sh"
        env_datei = Path(tempfile.mkdtemp()) / ".env"
        if inhalt is not None:
            env_datei.write_text(inhalt)
        r = subprocess.run(["bash", "-c", f'. "{lib}"; egress_token_sicherstellen "{env_datei}"; echo "rc=$?"'],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip(), env_datei.read_text() if env_datei.exists() else None

    def test_fehlt_wird_angelegt(self):
        aus, env = self._lauf("API_SECRET_KEY=x\n")
        self.assertEqual(aus, "rc=0")
        self.assertRegex(env, r"\nEGRESS_PROXY_TOKEN=[0-9a-f]{64}\n")
        self.assertIn("API_SECRET_KEY=x", env)

    def test_vorhanden_bleibt(self):
        alt = "EGRESS_PROXY_TOKEN=" + "a" * 64 + "\n"
        self.assertEqual(self._lauf(alt), ("rc=1", alt))

    def test_leer_oder_kurz_wird_ersetzt(self):
        for alt in ("EGRESS_PROXY_TOKEN=\n", "EGRESS_PROXY_TOKEN=kurz\n"):
            aus, env = self._lauf(alt)
            self.assertEqual(aus, "rc=0")
            self.assertRegex(env, r"^EGRESS_PROXY_TOKEN=[0-9a-f]{64}\n$")

    def test_ohne_env_nichts(self):
        self.assertEqual(self._lauf(None), ("rc=1", None))


if __name__ == "__main__":
    unittest.main()
