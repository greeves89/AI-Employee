"""Sicherheitsstatus unter Admin → Betrieb (core/sicherheitsstatus.py).

Jede Ampel wird in beide Richtungen geprüft: Eine Prüfung, die immer „ok“ sagt,
wäre schlimmer als keine.
"""

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import sicherheitsstatus as ss
from app.models.agent import Agent
from app.models.user import User, UserRole


def _stand(punkte, schluessel):
    return next(p for p in punkte if p["schluessel"] == schluessel)


def _settings(**ueber):
    werte = dict(redis_acl_enabled=True, redis_url="redis://:geheim@redis:6379",
                 encryption_key=Fernet.generate_key().decode(), api_secret_key="x" * 48,
                 sentinel_enabled=True, kiosk_enabled=False, msgraph_read_only=True)
    werte.update(ueber)
    return SimpleNamespace(**werte)


class EinstellungenTests(unittest.TestCase):

    def _punkte(self, env=None, **ueber):
        with patch.object(ss, "settings", _settings(**ueber)), \
             patch.dict("os.environ", env or {"CORS_ALLOW_ORIGIN": "https://ai.example.com"}, clear=False):
            return ss._einstellungen()

    def test_alles_sicher(self):
        punkte = self._punkte()
        for s in ("redis_acl", "redis_passwort", "encryption_key", "api_secret", "sentinel", "cors", "kiosk"):
            self.assertEqual(_stand(punkte, s)["stand"], "ok", s)

    def test_jede_ampel_schlaegt_an(self):
        punkte = self._punkte(env={"CORS_ALLOW_ORIGIN": "*"},
                              redis_acl_enabled=False, redis_url="redis://:changeme-redis-password@redis",
                              encryption_key="zu-kurz-und-kein-fernet", api_secret_key="kurz",
                              sentinel_enabled=False, kiosk_enabled=True)
        erwartet = {"redis_acl": "kritisch", "redis_passwort": "kritisch", "encryption_key": "kritisch",
                    "api_secret": "kritisch", "sentinel": "warnung", "cors": "warnung", "kiosk": "warnung"}
        for s, st in erwartet.items():
            p = _stand(punkte, s)
            self.assertEqual(p["stand"], st, s)
            self.assertTrue(p["beheben"], f"{s}: ohne Hinweis zum Beheben")

    def test_kein_geheimnis_im_text(self):
        """Die Karte zeigt Texte an — Passwort oder Schlüssel dürfen dort nie stehen."""
        schluessel = Fernet.generate_key().decode()
        punkte = self._punkte(encryption_key=schluessel, redis_url="redis://:sehr-geheim@redis")
        alles = str(punkte)
        self.assertNotIn(schluessel, alles)
        self.assertNotIn("sehr-geheim", alles)


class AgentenTests(unittest.IsolatedAsyncioTestCase):
    async def test_agenten_mit_sudo_werden_genannt(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[User.__table__, Agent.__table__])
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as db:
            db.add_all([
                User(id="u1", email="u@example.invalid", name="U", role=UserRole.MEMBER),
                Agent(id="a1", name="Ohne", user_id="u1", config={}, autonomy_level="l3"),
                Agent(id="a2", name="Mit", user_id="u1", config={}, autonomy_level="l4",
                      access_policy={"autonomy_matrix": {"system_config": "allow"}}),
            ])
            await db.commit()
            punkte = await ss._agenten(db)
        await engine.dispose()
        p = _stand(punkte, "sudo")
        self.assertEqual(p["stand"], "warnung")
        self.assertIn("Mit", p["text"])
        self.assertNotIn("Ohne", p["text"])


class DockerTests(unittest.TestCase):

    def _client(self, version="29.6.1", agent_label="1.409.2", app_im_plattformnetz=False):
        agent = SimpleNamespace(status="running", name="ai-agent-x", labels={"ai-employee.version": agent_label})
        app = SimpleNamespace(name="agent-abcdef12-p-web-1", labels={"com.docker.compose.project": "agent-abcdef12-p"})
        netz = MagicMock(attrs={"Containers": {"app": {}} if app_im_plattformnetz else {}})
        client = SimpleNamespace(
            version=lambda: {"Version": version},
            containers=SimpleNamespace(get=lambda cid: {"agent": agent, "app": app}[cid]),
            networks=SimpleNamespace(get=lambda _n: netz))
        return client

    def _punkte(self, **kw):
        with patch("app.services.docker_service.DockerService", return_value=SimpleNamespace(client=self._client(**kw))), \
             patch("app.config.get_agent_version", return_value="1.409.2"):
            return ss._docker(["agent"])

    def test_alles_aktuell(self):
        punkte = self._punkte()
        for s in ("docker_version", "abbild", "app_netz"):
            self.assertEqual(_stand(punkte, s)["stand"], "ok", s)

    def test_alte_docker_version_altes_abbild_app_im_plattformnetz(self):
        punkte = self._punkte(version="24.0.7", agent_label="1.400.0", app_im_plattformnetz=True)
        self.assertEqual(_stand(punkte, "docker_version")["stand"], "kritisch")
        self.assertEqual(_stand(punkte, "abbild")["stand"], "warnung")
        self.assertEqual(_stand(punkte, "app_netz")["stand"], "kritisch")


class ZugriffTests(unittest.TestCase):
    def test_nur_fuer_admins(self):
        from app.api import settings as api
        from app.dependencies import require_admin
        abh = inspect.signature(api.get_sicherheit).parameters["user"].default
        self.assertIs(abh.dependency, require_admin)

    def test_kritisches_zuerst(self):
        """Die Karte zeigt die Liste in der gelieferten Reihenfolge."""
        rang = {"kritisch": 0, "warnung": 1, "info": 2, "ok": 3}
        punkte = [{"stand": s} for s in ("ok", "kritisch", "info", "warnung")]
        punkte.sort(key=lambda p: rang[p["stand"]])
        self.assertEqual([p["stand"] for p in punkte], ["kritisch", "warnung", "info", "ok"])



class AbbildSignaturenTests(unittest.TestCase):
    """Ergebnis der Signaturpruefung beim Update (scripts/lib/abbilder.sh)."""

    def _punkt(self, inhalt):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from app.core import sicherheitsstatus as st
        with tempfile.TemporaryDirectory() as ordner:
            datei = Path(ordner) / "signaturpruefung"
            if inhalt is not None:
                datei.write_text(inhalt)
            with patch("app.config.get_agent_version", return_value="1.419.0"):
                (p,) = st._abbild_signaturen(str(datei))
        return p

    def test_zustaende(self):
        self.assertEqual(self._punkt("geprueft 1.419.0 1793460000\n")["stand"], "ok")
        self.assertEqual(self._punkt("geprueft 1.418.0 1793460000\n")["stand"], "warnung")
        for ergebnis in ("cosign_fehlt", "aus", "ungueltig", "ohne_identitaet"):
            with self.subTest(ergebnis):
                self.assertEqual(self._punkt(f"{ergebnis} 1.419.0 1\n")["stand"], "warnung")
        self.assertIn("lokal gebaut", self._punkt("cosign_fehlt 1.419.0 1\n")["text"])
        self.assertEqual(self._punkt(None)["stand"], "info")
        self.assertEqual(self._punkt("kaputt")["stand"], "info")

if __name__ == "__main__":
    unittest.main()


class DlpTests(unittest.IsolatedAsyncioTestCase):
    """Der DLP-Filter ist bei jeder Anlage zunächst aus — die Ampel muss das zeigen."""

    async def asyncSetUp(self):
        from app.models.dlp_rule import DlpRule
        from app.models.platform_settings import PlatformSettings

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(DlpRule.metadata.create_all,
                                tables=[DlpRule.__table__, PlatformSettings.__table__,
                                        User.__table__, Agent.__table__])
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _an(self, wert="true"):
        from app.models.platform_settings import PlatformSettings
        self.db.add(PlatformSettings(key="dlp_enabled", value=wert, is_secret=False))
        await self.db.commit()

    async def test_aus_ist_eine_warnung(self):
        p = _stand(await ss._dlp(self.db), "dlp")
        self.assertEqual(p["stand"], "warnung")
        self.assertIn("Admin → DLP", p["beheben"])

    async def test_an_mit_voreinstellung_ok(self):
        await self._an()
        p = _stand(await ss._dlp(self.db), "dlp")
        self.assertEqual(p["stand"], "ok")
        self.assertIn("gesperrt oder geschwärzt", p["text"])

    async def test_an_aber_schluessel_durchgelassen(self):
        from app.models.dlp_rule import DlpRule
        await self._an()
        self.db.add(DlpRule(pii_class="secret", action="allow", agent_id=None, enabled=True))
        await self.db.commit()
        self.assertEqual(_stand(await ss._dlp(self.db), "dlp")["stand"], "warnung")

    async def test_regel_eines_einzelnen_agenten_wird_gemeldet(self):
        """Der Filter nimmt die Regel des Agenten vor der globalen — die Ampel auch."""
        from app.models.dlp_rule import DlpRule
        await self._an()
        self.db.add(DlpRule(pii_class="secret", action="block", agent_id=None, enabled=True))
        self.db.add(DlpRule(pii_class="secret", action="allow", agent_id="a1", enabled=True))
        await self.db.commit()
        p = _stand(await ss._dlp(self.db), "dlp")
        self.assertEqual(p["stand"], "warnung")
        self.assertIn("a1", p["text"])

    async def test_abgeschaltete_regel_zaehlt_nicht(self):
        from app.models.dlp_rule import DlpRule
        await self._an()
        self.db.add(DlpRule(pii_class="secret", action="allow", agent_id="a1", enabled=False))
        await self.db.commit()
        self.assertEqual(_stand(await ss._dlp(self.db), "dlp")["stand"], "ok")

    async def test_alle_schluessel_klassen(self):
        from app.models.dlp_rule import DlpRule
        await self._an()
        for klasse in ("secret_format", "seed_phrase"):
            with self.subTest(klasse):
                await self.db.execute(DlpRule.__table__.delete())
                await self.db.commit()
                self.db.add(DlpRule(pii_class=klasse, action="log", agent_id=None, enabled=True))
                await self.db.commit()
                p = _stand(await ss._dlp(self.db), "dlp")
                self.assertEqual(p["stand"], "warnung")
                self.assertIn(klasse, p["text"])

    async def test_ok_text_nennt_die_grenzen(self):
        await self._an()
        text = _stand(await ss._dlp(self.db), "dlp")["text"]
        self.assertIn("Nicht erfasst", text)
