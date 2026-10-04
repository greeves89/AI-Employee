"""Datensicherung (#892): was gesichert wird, und dass man es sieht.

Befund aus dem Markttest: ``scripts/backup.sh`` sicherte nur Datenbank und Redis.
Es fehlten die Arbeitsordner der Agenten, der Verschlüsselungsschlüssel und die
``.env`` — nach einer Wiederherstellung wären alle verschlüsselten Geheimnisse
unlesbar und die Arbeitsordner weg. Und ob überhaupt gesichert wird, stand nirgends.

Geprüft wird hier:

* die Auswahl der Volumes (``scripts/lib/sicherung.sh``) — ausgeführt mit bash,
  ohne Docker: die Namen kommen über stdin;
* der Herzschlag ``POST /admin/backup-status`` mit lokalem Schlüssel und die
  Anzeige ``GET /admin/backup-status`` samt Ampel;
* der Concierge-Hinweis;
* der Selbsttest nach dem Rückspielen: ist ein Geheimnis mit dem vorhandenen
  Schlüssel wirklich lesbar?
"""

import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import admin as admin_api
from app.api import concierge
from app.config import settings
from app.core import attention, datensicherung, encryption, schluessel_selbsttest
from app.models.agent_secret import AgentSecret
from app.models.ai_account import AIAccount
from app.models.oauth_integration import OAuthIntegration
from app.models.platform_settings import PlatformSettings

REPO = Path(__file__).resolve().parents[2]
HILFSDATEI = REPO / "scripts" / "lib" / "sicherung.sh"

JETZT = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _auswahl(projekt: str, namen: list[str]) -> list[tuple[str, str]]:
    """Ruft ``volumes_auswaehlen`` aus der Hilfsdatei auf — wie backup.sh."""
    ausgabe = subprocess.run(
        ["bash", "-c", f'source "{HILFSDATEI}"; volumes_auswaehlen "$1"', "_", projekt],
        input="\n".join(namen) + "\n", capture_output=True, text=True, check=True,
    ).stdout
    return [tuple(z.split(" ", 1)) for z in ausgabe.splitlines() if z.strip()]


def _zurueck(projekt: str, archivname: str) -> str:
    return subprocess.run(
        ["bash", "-c", f'source "{HILFSDATEI}"; volume_fuer_archiv "$1" "$2"', "_", projekt, archivname],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


class VolumeAuswahlTests(unittest.TestCase):
    VORHANDEN = [
        "ai-employee_postgres_data", "ai-employee_redis_data", "ai-employee_feedback_data",
        "ai-employee_embedding_models", "ai-employee_stt_models",
        "workspace-ab12cd34", "claude-session-ab12cd34", "build-tools-ab12cd34",
        "ai-employee-shared",
        "anderes-projekt_redis_data", "fremdes_volume", "3f9a0c0e1b2d",
    ]

    def test_arbeitsordner_sitzungen_werkzeuge_und_gemeinsamer_ordner_sind_dabei(self):
        volumes = [v for _, v in _auswahl("ai-employee", self.VORHANDEN)]
        for erwartet in ("workspace-ab12cd34", "claude-session-ab12cd34",
                         "build-tools-ab12cd34", "ai-employee-shared",
                         "ai-employee_redis_data", "ai-employee_feedback_data"):
            with self.subTest(volume=erwartet):
                self.assertIn(erwartet, volumes)

    def test_caches_fremde_und_rohdaten_der_datenbank_fehlen(self):
        volumes = [v for _, v in _auswahl("ai-employee", self.VORHANDEN)]
        # Die Datenbank wird per pg_dump gesichert — ein tar der laufenden
        # Datendateien wäre nicht konsistent. Modelle lassen sich neu laden.
        for fehlt in ("ai-employee_postgres_data", "ai-employee_embedding_models",
                      "ai-employee_stt_models", "anderes-projekt_redis_data",
                      "fremdes_volume", "3f9a0c0e1b2d"):
            with self.subTest(volume=fehlt):
                self.assertNotIn(fehlt, volumes)

    def test_projektvolumes_werden_projektneutral_benannt_und_zurueckuebersetzt(self):
        paare = dict(_auswahl("ai-employee", self.VORHANDEN))
        self.assertEqual(paare["compose.redis_data"], "ai-employee_redis_data")
        # Rückspielen in eine Anlage mit anderem Verzeichnisnamen.
        self.assertEqual(_zurueck("neu", "compose.redis_data"), "neu_redis_data")
        self.assertEqual(_zurueck("neu", "workspace-ab12cd34"), "workspace-ab12cd34")

    def test_unsichere_namen_werden_nicht_weitergereicht(self):
        volumes = [v for _, v in _auswahl("ai-employee", ["workspace-../../etc", "workspace-a b"])]
        self.assertEqual(volumes, [])

    def test_skripte_sind_syntaktisch_in_ordnung(self):
        for name in ("backup.sh", "restore.sh", "update.sh", "lib/sicherung.sh"):
            with self.subTest(skript=name):
                subprocess.run(["bash", "-n", str(REPO / "scripts" / name)], check=True)


class AmpelTests(unittest.TestCase):
    def test_frische_sicherung_ist_in_ordnung(self):
        self.assertIsNone(attention.backup_state(JETZT - timedelta(hours=10), None, JETZT))

    def test_ueber_26_stunden_ist_gelb(self):
        self.assertEqual(attention.backup_state(JETZT - timedelta(hours=27), None, JETZT),
                         attention.WAITING)

    def test_ueber_drei_tage_ist_rot(self):
        self.assertEqual(attention.backup_state(JETZT - timedelta(days=4), None, JETZT),
                         attention.BROKEN)

    def test_nie_gesichert_ist_gelb(self):
        self.assertEqual(attention.backup_state(None, None, JETZT), attention.WAITING)

    def test_letzter_lauf_gescheitert_ist_rot(self):
        self.assertEqual(
            attention.backup_state(JETZT - timedelta(hours=2), JETZT - timedelta(hours=1), JETZT),
            attention.BROKEN,
        )


class _MitDatenbank(unittest.IsolatedAsyncioTestCase):
    TABELLEN = (PlatformSettings,)

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in self.TABELLEN:
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self._token = settings.backup_status_token
        settings.backup_status_token = "lokaler-schluessel-0123456789"

    async def asyncTearDown(self):
        settings.backup_status_token = self._token
        await self.engine.dispose()


class HerzschlagTests(_MitDatenbank):
    async def _melde(self, token, **felder):
        body = admin_api.BackupMeldung(**{"status": "ok", **felder})
        async with self.Session() as db:
            return await admin_api.backup_status_melden(body, x_backup_token=token, db=db)

    async def _lese(self):
        async with self.Session() as db:
            return await admin_api.backup_status_lesen(user=SimpleNamespace(id="a"), db=db)

    async def test_falscher_schluessel_wird_abgelehnt(self):
        with self.assertRaises(HTTPException) as ctx:
            await self._melde("falsch")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIsNone((await self._lese())["zuletzt_ok"])

    async def test_ohne_eingerichteten_schluessel_wird_nichts_angenommen(self):
        settings.backup_status_token = ""
        with self.assertRaises(HTTPException) as ctx:
            await self._melde("")
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_erfolgreiche_meldung_erscheint_mit_gruener_ampel(self):
        await self._melde(settings.backup_status_token, groesse_bytes=2048, volumes=5, dauer_s=12)
        stand = await self._lese()
        self.assertIsNotNone(stand["zuletzt_ok"])
        self.assertEqual(stand["groesse_bytes"], 2048)
        self.assertEqual(stand["volumes"], 5)
        self.assertEqual(stand["ampel"], "ok")
        self.assertTrue(stand["eingerichtet"])

    async def test_fehlschlag_nach_erfolg_macht_die_ampel_rot_und_nennt_den_schritt(self):
        await self._melde(settings.backup_status_token, groesse_bytes=1)
        await self._melde(settings.backup_status_token, status="fehler", schritt="Datenbank")
        stand = await self._lese()
        self.assertEqual(stand["ampel"], "rot")
        self.assertEqual(stand["letzter_fehler"]["schritt"], "Datenbank")
        self.assertIsNotNone(stand["zuletzt_ok"], "der letzte Erfolg bleibt sichtbar")
        # Der nächste Erfolg hebt den Fehler wieder auf.
        await self._melde(settings.backup_status_token)
        self.assertEqual((await self._lese())["ampel"], "ok")

    async def test_nie_gemeldet_ist_gelb(self):
        stand = await self._lese()
        self.assertEqual(stand["ampel"], "gelb")

    async def test_concierge_nennt_die_fehlende_sicherung(self):
        async with self.Session() as db:
            punkte = await concierge._collect_attention(db, [], JETZT, 0, [], [])
        sicherung = [p for p in punkte if p["kind"] == "backup"]
        self.assertEqual(len(sicherung), 1)
        self.assertEqual(sicherung[0]["severity"], attention.WAITING)

    async def test_concierge_schweigt_bei_frischer_sicherung(self):
        async with self.Session() as db:
            await datensicherung.melde(db, status="ok", now=JETZT - timedelta(hours=1))
            await db.commit()
            punkte = await concierge._collect_attention(db, [], JETZT, 0, [], [])
        self.assertEqual([p for p in punkte if p["kind"] == "backup"], [])


class SchluesselSelbsttestTests(_MitDatenbank):
    TABELLEN = (AgentSecret, OAuthIntegration, AIAccount)

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._alt = (settings.encryption_key, encryption._cached_key)

    async def asyncTearDown(self):
        settings.encryption_key, encryption._cached_key = self._alt
        await super().asyncTearDown()

    def _schluessel(self, key: str):
        settings.encryption_key = key
        encryption._cached_key = None

    async def _lege_geheimnis_an(self):
        async with self.Session() as db:
            db.add(AIAccount(name="Konto", provider_type="anthropic",
                             api_key_encrypted=encryption.encrypt_token("sk-test")))
            await db.commit()

    async def test_mit_passendem_schluessel_lesbar(self):
        self._schluessel(Fernet.generate_key().decode())
        await self._lege_geheimnis_an()
        async with self.Session() as db:
            ergebnis = await schluessel_selbsttest.pruefe(db)
        self.assertEqual(ergebnis, {"geprueft": 1, "lesbar": 1, "ok": True})

    async def test_mit_anderem_schluessel_schlaegt_er_an(self):
        self._schluessel(Fernet.generate_key().decode())
        await self._lege_geheimnis_an()
        self._schluessel(Fernet.generate_key().decode())
        async with self.Session() as db:
            ergebnis = await schluessel_selbsttest.pruefe(db)
        self.assertEqual(ergebnis, {"geprueft": 1, "lesbar": 0, "ok": False})

    async def test_ohne_geheimnisse_gibt_es_nichts_zu_pruefen(self):
        self._schluessel(Fernet.generate_key().decode())
        async with self.Session() as db:
            ergebnis = await schluessel_selbsttest.pruefe(db)
        self.assertEqual(ergebnis, {"geprueft": 0, "lesbar": 0, "ok": True})


if __name__ == "__main__":
    unittest.main()
