"""Modell-Entdeckung: ein nicht beantworteter Abruf darf den guten Stand nicht loeschen (#984).

Die Lage vorher: ``discover()`` schrieb das Ergebnis der Anbieter-Abrufe ohne
Ansehen der Ursache in EINEN Einstellungs-Schluessel (``model_discovery_cache``)
und setzte dabei einen frischen ``discovered_at``-Stempel. Alle Abrufe fangen jede
Ausnahme selbst ab und liefern dann eine leere Liste. Ein Knopfdruck waehrend eines
Netzaussetzers erzeugte damit zwei Falschaussagen in einem Schreibvorgang: die
entdeckten Modelle waren weg (es gibt keinen zweiten Stand, aus dem sich das heilen
koennte), und die Oberflaeche zeigte "gerade gesucht" mit null Treffern — nicht
unterscheidbar von "es gibt wirklich nichts".

DIE REGEL, die hier festgenagelt wird: ein Anbieter raeumt seinen Stand nur auf,
wenn er GEFRAGT WURDE und GEANTWORTET hat.

Die Tests pruefen beide Richtungen. Nur "alter Stand bleibt" waere zu wenig: ein
Fix, der den Stand immer behaelt, macht ein zurueckgezogenes Modell unsterblich und
friert den Stempel ein. Neben jedem Erhaltungsfall steht darum der Gegenfall, in dem
der Abruf GEKLAPPT hat — dort muss aufgeraeumt und der Stempel gesetzt werden.
"""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.core.verbindungswiederholung import PAUSEN_HINTERGRUND_S, PAUSEN_S
from app.models.platform_settings import PlatformSettings
from app.services import model_registry_service as mrs
from app.services.model_registry_service import (
    CACHE_KEY,
    Abruf,
    _discover_anthropic,
    _discover_foundry,
    discover,
    get_admin_catalog,
)
from app.services.settings_service import SettingsService

ALT_STEMPEL = "2026-10-01T08:00:00+00:00"


def _modell(provider, value, label="Alt"):
    return {
        "mode": "claude_code", "provider": provider, "value": value,
        "label": label, "tier": "Discovered", "source": "discovered",
    }


MODELL_ANTHROPIC = _modell("anthropic", "claude-entdeckt-alt", "Alt (anthropic)")
MODELL_FOUNDRY = _modell("foundry", "claude-foundry-alt", "Alt (foundry)")


def _client_returning(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


class EntdeckungAusfallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                PlatformSettings.metadata.create_all, tables=[PlatformSettings.__table__]
            )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _alten_stand_legen(self, modelle=(MODELL_ANTHROPIC, MODELL_FOUNDRY), stempel=ALT_STEMPEL):
        async with self.Session() as db:
            await SettingsService(db).set(
                CACHE_KEY, json.dumps({"discovered_at": stempel, "models": list(modelle)}),
            )
            await db.commit()

    async def _discover_mit(self, *, anthropic, foundry, anthropic_befragt=True, user_id=None):
        """``discover()`` fahren, die Anbieter-Abrufe als Doppel gesetzt.

        ``anthropic_befragt=False`` ist der Fall "dieser Administrator hat keine
        eigenen Claude-Zugangsdaten" — der Abruf findet dann gar nicht statt.
        """
        self.anthropic_doppel = AsyncMock(return_value=anthropic)
        async with self.Session() as db:
            with patch.object(mrs, "_discover_anthropic", self.anthropic_doppel), \
                 patch.object(mrs, "_discover_foundry", AsyncMock(return_value=foundry)), \
                 patch.object(
                     mrs, "_anthropic_auth_headers",
                     AsyncMock(return_value={"x-api-key": "k"} if anthropic_befragt else None),
                 ), \
                 patch.object(settings, "foundry_api_key", "fk"), \
                 patch.object(settings, "foundry_resource", "res"):
                ergebnis = await discover(db, user_id)
        async with self.Session() as db:
            roh = await SettingsService(db).get(CACHE_KEY)
            katalog = await get_admin_catalog(db)
        return ergebnis, json.loads(roh), katalog

    # ---------------------------------------------------------------- Erhaltung

    async def test_gescheiterter_anbieter_behaelt_seinen_stand_und_den_stempel(self):
        """Der Kern von #984: anthropic nicht erreichbar, foundry hat geantwortet."""
        await self._alten_stand_legen()
        ergebnis, stand, katalog = await self._discover_mit(
            anthropic=Abruf([], gescheitert=True, grund="Verbindung nicht zustande gekommen"),
            foundry=Abruf([], gescheitert=False),
        )
        werte = [m["value"] for m in stand["models"]]
        self.assertIn(MODELL_ANTHROPIC["value"], werte,
                      "Der Stand des nicht erreichten Anbieters wurde geloescht")
        self.assertNotIn(MODELL_FOUNDRY["value"], werte,
                         "Ein erfolgreich abgefragter Anbieter darf aufraeumen")
        self.assertEqual(stand["discovered_at"], ALT_STEMPEL,
                         "Der Stempel des vollstaendigen Standes ruckte trotz Ausfall weiter")
        self.assertEqual(stand["providers_failed"],
                         {"anthropic": "Verbindung nicht zustande gekommen"})
        self.assertEqual(stand["providers_kept"], ["anthropic"])
        self.assertEqual(ergebnis["last_discovery"]["kept_from_cache"], 1)
        # ... und das ueberlebt ein Neuladen der Seite, nicht nur die POST-Antwort.
        self.assertEqual(katalog["discovered_at"], ALT_STEMPEL)
        self.assertEqual(katalog["providers_failed"],
                         {"anthropic": "Verbindung nicht zustande gekommen"})
        self.assertEqual(katalog["providers_kept"], ["anthropic"])
        self.assertTrue(katalog["last_attempt_at"], "Der Versuch selbst muss sichtbar sein")
        self.assertNotEqual(katalog["last_attempt_at"], ALT_STEMPEL)

    async def test_nicht_befragter_anbieter_verliert_seinen_stand_nicht(self):
        """Zweiter Administrator ohne eigene Zugangsdaten drueckt den Knopf.

        ``_anthropic_auth_headers`` faellt auf die PERSOENLICHE Zugangsdatei des
        Aufrufers zurueck. Ohne diese Haelfte der Regel loescht Administrator B
        mit einem Knopfdruck, was Administrator A gefunden hat — und setzt dabei
        noch einen frischen Stempel.
        """
        await self._alten_stand_legen()
        _ergebnis, stand, _k = await self._discover_mit(
            anthropic=Abruf([], gescheitert=False),   # nicht gescheitert: gar nicht gefragt
            foundry=Abruf([], gescheitert=False),
            anthropic_befragt=False,
        )
        self.assertEqual([m["value"] for m in stand["models"]], [MODELL_ANTHROPIC["value"]])
        self.assertEqual(stand["discovered_at"], ALT_STEMPEL)
        self.assertEqual(stand["providers_kept"], ["anthropic"])
        self.assertEqual(stand["providers_failed"], {},
                         "Nicht gefragt ist kein Ausfall — das waere eine Falschmeldung")

    async def test_unvollstaendiger_eintrag_wird_nicht_mitgeschleppt(self):
        """Ein Datensatz ohne ``label`` beendet den Katalogaufbau mit KeyError.

        Vor dieser Wache wurde er beim Ausfall des Anbieters uebernommen UND
        festgeschrieben — der Knopf, der den Zustand vorher heilte, zementierte ihn.
        """
        await self._alten_stand_legen(modelle=(
            {"mode": "claude_code", "provider": "anthropic", "value": "claude-kaputt"},
            MODELL_ANTHROPIC,
        ))
        _ergebnis, stand, katalog = await self._discover_mit(
            anthropic=Abruf([], gescheitert=True, grund="HTTP 500"),
            foundry=Abruf([], gescheitert=False),
        )
        werte = [m["value"] for m in stand["models"]]
        self.assertEqual(werte, [MODELL_ANTHROPIC["value"]])
        self.assertNotIn("claude-kaputt", werte)
        self.assertTrue(katalog["modes"], "Der Katalog muss trotzdem aufgebaut werden")

    async def test_kaputter_eintrag_bricht_den_katalog_nicht(self):
        """Dieselbe Wache auf dem Leseweg: ``GET /agents/models`` trifft JEDEN Nutzer."""
        await self._alten_stand_legen(modelle=(
            {"mode": "claude_code", "provider": "anthropic", "value": "claude-kaputt"},
        ))
        async with self.Session() as db:
            katalog = await get_admin_catalog(db)
        self.assertTrue(katalog["modes"])

    # ------------------------------------------------------------- Gegenrichtung

    async def test_erfolgreicher_leerer_abruf_raeumt_auf_und_setzt_den_stempel(self):
        """Ohne diesen Test waere ein Fix, der immer alles behaelt, gruen."""
        await self._alten_stand_legen()
        ergebnis, stand, _k = await self._discover_mit(
            anthropic=Abruf([], gescheitert=False),
            foundry=Abruf([], gescheitert=False),
        )
        self.assertEqual(stand["models"], [])
        self.assertNotEqual(stand["discovered_at"], ALT_STEMPEL)
        self.assertEqual(stand["discovered_at"], stand["last_attempt_at"])
        self.assertEqual(stand["providers_failed"], {})
        self.assertEqual(stand["providers_kept"], [])
        self.assertEqual(ergebnis["last_discovery"]["kept_from_cache"], 0)

    async def test_altlast_eines_nie_befragten_anbieters_verschwindet(self):
        """openai wird bewusst NICHT entdeckt — alte openai-Funde sind Altlast.

        Gegenprobe zur Regel: "nicht befragt" darf nicht zum Freibrief werden,
        unter dem ein laengst stillgelegter Suchweg seine Funde ewig haelt.
        """
        await self._alten_stand_legen(modelle=(_modell("openai", "gpt-5.5-alt"),))
        _e, stand, _k = await self._discover_mit(
            anthropic=Abruf([], gescheitert=False), foundry=Abruf([], gescheitert=False),
        )
        self.assertEqual(stand["models"], [])
        self.assertNotEqual(stand["discovered_at"], ALT_STEMPEL)

    async def test_frischer_treffer_gewinnt_und_wird_nicht_als_uebernahme_gezaehlt(self):
        """Teilergebnis des gescheiterten Abrufs ist aktueller als der Cache.

        Und: ``kept_from_cache`` muss NACH der Entdopplung gezaehlt werden, sonst
        behauptet die Oberflaeche eine Uebernahme, die nicht stattgefunden hat.
        """
        await self._alten_stand_legen(modelle=(MODELL_FOUNDRY,))
        frisch = {**MODELL_FOUNDRY, "label": "Frisch gelesen"}
        ergebnis, stand, _k = await self._discover_mit(
            anthropic=Abruf([], gescheitert=False),
            foundry=Abruf([frisch], gescheitert=True, grund="HTTP 502"),
        )
        self.assertEqual(len(stand["models"]), 1, "Der Eintrag wurde doppelt abgelegt")
        self.assertEqual(stand["models"][0]["label"], "Frisch gelesen")
        self.assertEqual(ergebnis["last_discovery"]["kept_from_cache"], 0)
        self.assertEqual(stand["providers_kept"], [])
        # Der Stempel bleibt trotzdem stehen: der Abruf war abgebrochen.
        self.assertEqual(stand["discovered_at"], ALT_STEMPEL)
        self.assertEqual(stand["providers_failed"], {"foundry": "HTTP 502"})

    async def test_erster_lauf_mit_ausfall_meldet_den_versuch_statt_zu_schweigen(self):
        """Kein alter Stand UND Ausfall: "gerade gesucht, 0 gefunden" waere die Luege.

        Umgekehrt darf die Oberflaeche aber auch nicht "noch nie gesucht" sagen —
        dafuer gibt es ``last_attempt_at``.
        """
        ergebnis, stand, katalog = await self._discover_mit(
            anthropic=Abruf([], gescheitert=True, grund="HTTP 401"),
            foundry=Abruf([_modell("foundry", "claude-neu")], gescheitert=False),
        )
        self.assertIsNone(stand["discovered_at"])
        self.assertTrue(stand["last_attempt_at"])
        self.assertEqual(stand["providers_failed"], {"anthropic": "HTTP 401"})
        self.assertEqual([m["value"] for m in stand["models"]], ["claude-neu"])
        self.assertIsNone(katalog["discovered_at"])
        self.assertEqual(katalog["last_attempt_at"], stand["last_attempt_at"])
        self.assertEqual(ergebnis["last_discovery"]["foundry_found"], 1)

    async def test_die_zugangsdaten_des_aufrufers_werden_durchgereicht(self):
        """Ohne ``db``/``user_id`` findet der Abruf auf den meisten Anlagen nichts.

        Die persoenliche Zugangsdatei des aufrufenden Administrators ist laut
        ``_anthropic_auth_headers`` der Normalfall; ein Wegfall dieser beiden
        Argumente wuerde stumm "0 erkannt" liefern.
        """
        await self._discover_mit(
            anthropic=Abruf([], gescheitert=False), foundry=Abruf([], gescheitert=False),
            user_id="admin-7",
        )
        self.anthropic_doppel.assert_awaited_once_with(unittest.mock.ANY, "admin-7")


class AbrufMeldetAusfallTests(unittest.IsolatedAsyncioTestCase):
    """Die Quelle der Wahrheit: meldet der Abruf selbst, dass er gescheitert ist?

    Je Anbieter EIGENE Faelle: die Mutationsbatterie zu #984 hat gezeigt, dass ein
    Fehler im Anthropic-Zweig unbemerkt bleibt, wenn nur Foundry geprueft wird —
    und Anthropic ist der Zweig, der auf jeder Anlage laeuft.
    """

    def _ohne_pausen(self):
        return patch.object(mrs, "PAUSEN_S", (0.0,))

    async def test_foundry_verbindungsfehler_mit_grund(self):
        ctx, client = _client_returning({})
        client.get = AsyncMock(side_effect=httpx.ConnectError("Temporary failure in name resolution"))
        with patch.object(settings, "foundry_api_key", "fk"), \
             patch.object(settings, "foundry_resource", "res"), \
             self._ohne_pausen(), patch("httpx.AsyncClient", return_value=ctx):
            ergebnis = await _discover_foundry()
        self.assertEqual(ergebnis, Abruf([], True, "Verbindung nicht zustande gekommen"))

    async def test_anthropic_verbindungsfehler_mit_grund(self):
        ctx, client = _client_returning({})
        client.get = AsyncMock(side_effect=httpx.ConnectError("name resolution"))
        with patch.object(mrs, "_anthropic_auth_headers", AsyncMock(return_value={"x-api-key": "k"})), \
             self._ohne_pausen(), patch("httpx.AsyncClient", return_value=ctx):
            ergebnis = await _discover_anthropic()
        self.assertEqual(ergebnis, Abruf([], True, "Verbindung nicht zustande gekommen"))

    async def test_abgelehnter_schluessel_nennt_den_statuscode(self):
        """401 ist ein Dauerzustand, kein Aussetzer — der Administrator muss das
        am Grund erkennen koennen, sonst wartet er ewig auf Selbstheilung."""
        ctx, client = _client_returning({})
        antwort = MagicMock(status_code=401)
        client.get = AsyncMock(side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=antwort))
        with patch.object(mrs, "_anthropic_auth_headers", AsyncMock(return_value={"x-api-key": "k"})), \
             self._ohne_pausen(), patch("httpx.AsyncClient", return_value=ctx):
            ergebnis = await _discover_anthropic()
        self.assertEqual(ergebnis, Abruf([], True, "HTTP 401"))

    async def test_ohne_zugangsdaten_ist_nicht_gescheitert(self):
        with patch.object(mrs, "_anthropic_auth_headers", AsyncMock(return_value=None)):
            self.assertEqual(await _discover_anthropic(), Abruf([], gescheitert=False, grund=""))
        with patch.object(settings, "foundry_api_key", ""), \
             patch.object(settings, "foundry_resource", ""):
            self.assertEqual(await _discover_foundry(), Abruf([], gescheitert=False, grund=""))

    async def test_foundry_aussetzer_wird_wiederholt(self):
        ctx, client = _client_returning({"data": [{"id": "claude-opus-5"}]})
        gute_antwort = client.get.return_value
        client.get = AsyncMock(side_effect=[httpx.ConnectError("boom"), gute_antwort])
        with patch.object(settings, "foundry_api_key", "fk"), \
             patch.object(settings, "foundry_resource", "res"), \
             self._ohne_pausen(), patch("httpx.AsyncClient", return_value=ctx):
            ergebnis = await _discover_foundry()
        self.assertEqual(client.get.await_count, 2, "Es gab keinen zweiten Versuch")
        self.assertFalse(ergebnis.gescheitert)
        self.assertEqual([m["value"] for m in ergebnis.modelle], ["claude-opus-5"])

    async def test_anthropic_aussetzer_wird_wiederholt(self):
        ctx, client = _client_returning({"data": [{"id": "claude-opus-5"}]})
        gute_antwort = client.get.return_value
        client.get = AsyncMock(side_effect=[httpx.ConnectError("boom"), gute_antwort])
        with patch.object(mrs, "_anthropic_auth_headers", AsyncMock(return_value={"x-api-key": "k"})), \
             self._ohne_pausen(), patch("httpx.AsyncClient", return_value=ctx):
            ergebnis = await _discover_anthropic()
        self.assertEqual(client.get.await_count, 2)
        self.assertFalse(ergebnis.gescheitert)
        self.assertEqual([m["value"] for m in ergebnis.modelle], ["claude-opus-5"])


class LeiterUndZeitschrankeTests(unittest.IsolatedAsyncioTestCase):
    """Ein Mensch wartet vor dem Knopf: 2,0 s Deckung, nicht 40 s — und der
    Verbindungsaufbau darf nicht dreimal die volle Antwort-Zeitschranke kosten."""

    async def _spion_laeuft(self, aufruf):
        gesehen = {}

        async def spion(senden, *, zweck, pausen=None):
            gesehen["zweck"] = zweck
            gesehen["pausen"] = pausen
            return await senden()

        ctx, _client = _client_returning({"data": []})
        with patch.object(mrs, "mit_verbindungswiederholung", spion), \
             patch.object(settings, "foundry_api_key", "fk"), \
             patch.object(settings, "foundry_resource", "res"), \
             patch.object(mrs, "_anthropic_auth_headers", AsyncMock(return_value={"x-api-key": "k"})), \
             patch("httpx.AsyncClient", return_value=ctx):
            await aufruf()
        return gesehen

    async def test_foundry_nutzt_die_kurze_leiter(self):
        gesehen = await self._spion_laeuft(_discover_foundry)
        self.assertEqual(gesehen["pausen"], PAUSEN_S)
        self.assertNotEqual(gesehen["pausen"], PAUSEN_HINTERGRUND_S)
        self.assertTrue(gesehen["zweck"], "Ohne Zweck ist die Logzeile nicht zuzuordnen")

    async def test_anthropic_nutzt_die_kurze_leiter(self):
        gesehen = await self._spion_laeuft(_discover_anthropic)
        self.assertEqual(gesehen["pausen"], PAUSEN_S)
        self.assertNotEqual(gesehen["pausen"], PAUSEN_HINTERGRUND_S)
        self.assertTrue(gesehen["zweck"])

    async def test_verbindungsaufbau_hat_eine_eigene_kurze_schranke(self):
        """Sonst sind drei Versuche je Anbieter 45 s Wartezeit — und hinter einem
        Reverse-Proxy mit 60-s-Leseschranke ein 504 ohne jedes Ergebnis."""
        self.assertIsInstance(mrs._HTTP_TIMEOUT, httpx.Timeout)
        self.assertEqual(mrs._HTTP_TIMEOUT.connect, 5.0)
        versuche = len(PAUSEN_S) + 1
        self.assertLessEqual(
            versuche * mrs._HTTP_TIMEOUT.connect + sum(PAUSEN_S), 20.0,
            "Schlimmster Fall je Anbieter ueber 20 s — zu lang fuer einen HTTP-Pfad",
        )


if __name__ == "__main__":
    unittest.main()
