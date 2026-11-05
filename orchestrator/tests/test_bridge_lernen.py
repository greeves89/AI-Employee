"""„Aus meiner Arbeit lernen“ (#1023): Protokoll, Auswertung, Regelvorschläge.

Festgehalten wird, was Datenschutz und Vertrauen ausmacht:
* Ohne eingeschalteten Schalter landet NICHTS im Protokoll — egal, was die Bridge schickt.
* Private Fenster (Passwortmanager, Anmeldung, Banking) kommen nicht hinein.
* Aus heisst aus UND weg: Ausschalten löscht das Protokoll.
* Das Protokoll gehört dem Menschen: fremde sieht, löscht und beantwortet niemand.
* Der Agent sieht nur die Zusammenfassung; ein Vorschlag mit erfundener Bedingung fällt durch.
* Annehmen legt die Regel an (und nur ein Mensch kann annehmen), Ablehnen nicht.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api import computer_use as cu
from app.core import bridge_lernen as bl
from app.core import bridge_mitdenker as bm
from app.models.bridge_lernen import BridgeLernen, BridgeNutzung, BridgeVorschlag
from app.models.bridge_regel import BridgeRegel
from app.models.user import UserRole
from tests.test_projekte import Basis

A = SimpleNamespace(id="ua", email="a@example.invalid", role=UserRole.MEMBER)
B = SimpleNamespace(id="ub", email="b@example.invalid", role=UserRole.MEMBER)
JETZT = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


class _Lern(Basis):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.engine.begin() as conn:
            for m in (BridgeRegel, BridgeLernen, BridgeNutzung, BridgeVorschlag):
                await conn.run_sync(m.metadata.create_all, tables=[m.__table__])
        self.p_db = patch("app.db.session.async_session_factory", self.Session)
        self.p_db.start()
        self.konfig = patch.object(cu, "beobachter_konfig_an_nutzer", AsyncMock())
        self.konfig_mock = self.konfig.start()

    async def asyncTearDown(self):
        self.konfig.stop()
        self.p_db.stop()
        await super().asyncTearDown()

    async def _an(self, db, user="ua"):
        await bl.schalter_setzen(db, user, True, "a1")
        await db.commit()

    async def _gewohnheit(self, db, user="ua", tage=3, app="Google Chrome", titel="YouTube Studio - Kanal"):
        for t in range(tage):
            for _ in range(2):
                await bl.protokoll_aufnehmen(db, user, app, titel, 120, jetzt=JETZT - timedelta(days=t))
        await db.commit()


class Protokoll(_Lern):
    async def test_ohne_schalter_wird_nichts_gespeichert(self):
        async with self.Session() as db:
            z = await bl.protokoll_aufnehmen(db, "ua", "Chrome", "Start", 60)
            await db.commit()
            self.assertIsNone(z)
            self.assertEqual(await bl.protokoll_anzahl(db, "ua"), 0)

    async def test_mit_schalter_nur_app_titel_dauer(self):
        async with self.Session() as db:
            await self._an(db)
            await bl.protokoll_aufnehmen(db, "ua", "  Chrome ", "Start\n seite", "60")
            await db.commit()
            eintraege = await bl.protokoll_lesen(db, "ua")
        self.assertEqual(len(eintraege), 1)
        self.assertEqual(set(eintraege[0]), {"id", "app", "titel", "dauer_sekunden", "ende"})
        self.assertEqual((eintraege[0]["app"], eintraege[0]["titel"]), ("Chrome", "Start seite"))

    async def test_private_fenster_und_unsinn_kommen_nicht_hinein(self):
        async with self.Session() as db:
            await self._an(db)
            for app, titel, dauer in [("1Password", "Tresor", 60), ("Chrome", "Anmelden - Bank", 60),
                                      ("Chrome", "Start", 0), ("Chrome", "Start", "abc"), ("", "", 60)]:
                self.assertIsNone(await bl.protokoll_aufnehmen(db, "ua", app, titel, dauer), (app, titel, dauer))

    async def test_dauer_wird_begrenzt(self):
        async with self.Session() as db:
            await self._an(db)
            z = await bl.protokoll_aufnehmen(db, "ua", "Chrome", "Start", 10**9)
        self.assertEqual(z.dauer_sekunden, bl.MAX_DAUER_SEKUNDEN)

    async def test_ausschalten_loescht_das_protokoll(self):
        async with self.Session() as db:
            await self._an(db)
            await self._gewohnheit(db)
            r = await cu.lernen_schalten(cu.LernenEingabe(aktiv=False), user=A, db=db)
            self.assertFalse(r["aktiv"])
            self.assertEqual(await bl.protokoll_anzahl(db, "ua"), 0)
        self.konfig_mock.assert_awaited_with("ua")

    async def test_einsehen_und_loeschen_nur_das_eigene(self):
        async with self.Session() as db:
            await self._an(db)
            await self._gewohnheit(db)
            await bl.schalter_setzen(db, "ub", True)
            await bl.protokoll_aufnehmen(db, "ub", "Mail", "Posteingang", 60)
            await db.commit()
            meins = await cu.lernen_protokoll(user=A, db=db)
            fremd = await cu.lernen_protokoll(user=B, db=db)
            self.assertEqual(meins["gesamt"], 6)
            self.assertEqual(fremd["gesamt"], 1)
            await cu.lernen_protokoll_loeschen(user=A, db=db)
            self.assertEqual(await bl.protokoll_anzahl(db, "ua"), 0)
            self.assertEqual(await bl.protokoll_anzahl(db, "ub"), 1)

    async def test_aufraeumen_nach_aufbewahrung_und_ohne_schalter(self):
        async with self.Session() as db:
            await self._an(db)
            await bl.protokoll_aufnehmen(db, "ua", "Chrome", "alt", 60, jetzt=JETZT - timedelta(days=40))
            await bl.protokoll_aufnehmen(db, "ua", "Chrome", "neu", 60, jetzt=JETZT)
            db.add(BridgeNutzung(user_id="ub", app="Mail", titel="x", dauer_sekunden=5, ende_at=JETZT))
            await bl.schalter_setzen(db, "ub", False)
            await db.commit()
            await bl.protokoll_aufraeumen(db, JETZT)
            await db.commit()
            self.assertEqual([e["titel"] for e in await bl.protokoll_lesen(db, "ua")], ["neu"])
            self.assertEqual(await bl.protokoll_anzahl(db, "ub"), 0)

    async def test_nutzung_vom_websocket_braucht_mitsehen_und_schalter(self):
        async with self.Session() as db:
            await self._an(db)
        nachricht = {"app": "Chrome", "titel": "Start", "dauer_sekunden": 30}
        ohne = {"user_id": "ua", "allowed_capabilities": set()}
        await cu._nutzung_buchen(ohne, nachricht)
        async with self.Session() as db:
            self.assertEqual(await bl.protokoll_anzahl(db, "ua"), 0)
        mit = {"user_id": "ua", "allowed_capabilities": {cu.OBSERVE_CAP}, "agent_id": "a2"}
        await cu._nutzung_buchen(mit, nachricht)
        async with self.Session() as db:
            self.assertEqual(await bl.protokoll_anzahl(db, "ua"), 1)
            self.assertEqual((await bl.schalter_holen(db, "ua")).agent_id, "a2")


class Zusammenfassung(_Lern):
    async def test_nur_wiederkehrendes_zaehlt(self):
        async with self.Session() as db:
            await self._an(db)
            await self._gewohnheit(db)
            await bl.protokoll_aufnehmen(db, "ua", "Notizen", "Einmalig", 600, jetzt=JETZT)
            await db.commit()
            z = await bl.zusammenfassung(db, "ua", JETZT)
        self.assertEqual([f["titel"] for f in z["fenster"]], ["YouTube Studio - Kanal"])
        self.assertEqual(z["fenster"][0]["tage"], 3)
        self.assertTrue(bl.hat_stoff(z))

    async def test_zaehler_im_titel_trennen_nicht(self):
        async with self.Session() as db:
            await self._an(db)
            for t in range(3):
                for n in (1, 2):
                    await bl.protokoll_aufnehmen(db, "ua", "Mail", f"Posteingang ({t}{n})", 30,
                                                 jetzt=JETZT - timedelta(days=t))
            await db.commit()
            z = await bl.zusammenfassung(db, "ua", JETZT)
        self.assertEqual(len(z["fenster"]), 1)
        self.assertEqual(z["fenster"][0]["besuche"], 6)

    async def test_zu_wenig_ist_kein_stoff(self):
        async with self.Session() as db:
            await self._an(db)
            await bl.protokoll_aufnehmen(db, "ua", "Chrome", "Start", 60, jetzt=JETZT)
            await db.commit()
            self.assertFalse(bl.hat_stoff(await bl.zusammenfassung(db, "ua", JETZT)))

    async def test_anweisung_enthaelt_nur_die_zusammenfassung(self):
        async with self.Session() as db:
            await self._an(db)
            await self._gewohnheit(db)
            z = await bl.zusammenfassung(db, "ua", JETZT)
        text = bl.auswertung_anweisung(z, [])
        self.assertIn("YouTube Studio", text)
        self.assertIn("FREMDER INHALT", text)
        self.assertNotIn("bild", text.lower().replace("kein bildschirminhalt", ""))


Z = {"apps": [{"app": "Google Chrome", "besuche": 6, "tage": 3, "minuten": 12}],
     "fenster": [{"app": "Google Chrome", "titel": "YouTube Studio - Kanal", "besuche": 6, "tage": 3, "minuten": 12}]}


def _antwort(**felder):
    v = {"name": "Studio", "wenn_app": "Chrome", "wenn_titel": "YouTube Studio",
         "angebot": "Soll ich Titel vorschlagen?", "auftrag": "Schlage drei Titel vor.", "begruendung": "3 Tage"}
    v.update(felder)
    return json.dumps({"vorschlaege": [v]})


class VorschlaegeLesen(unittest.TestCase):
    def test_gueltiger_vorschlag(self):
        r = bl.vorschlaege_lesen("Hier: " + _antwort(), Z)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["wenn_titel"], "YouTube Studio")

    def test_nichts_fehler_und_unlesbares(self):
        for a in (None, "", "NICHTS", "[Fehler: Redis]", "kein json", '{"vorschlaege": "x"}', "[1]"):
            self.assertEqual(bl.vorschlaege_lesen(a, Z), [], a)

    def test_erfundene_bedingung_faellt_durch(self):
        self.assertEqual(bl.vorschlaege_lesen(_antwort(wenn_titel="Steuererklärung"), Z), [])
        self.assertEqual(bl.vorschlaege_lesen(_antwort(wenn_app="Excel"), Z), [])
        self.assertEqual(bl.vorschlaege_lesen(_antwort(wenn_app="", wenn_titel=""), Z), [])

    def test_zu_langer_auftrag_und_leeres_angebot_fallen_durch(self):
        self.assertEqual(bl.vorschlaege_lesen(_antwort(auftrag="x" * (bm.MAX_AUFTRAG + 1)), Z), [])
        self.assertEqual(bl.vorschlaege_lesen(_antwort(angebot=""), Z), [])

    def test_private_bedingung_faellt_durch(self):
        z = {"apps": [], "fenster": [{"app": "Chrome", "titel": "Login Bank", "besuche": 5, "tage": 3, "minuten": 1}]}
        self.assertEqual(bl.vorschlaege_lesen(_antwort(wenn_titel="Login", wenn_app=""), z), [])

    def test_hoechstens_drei(self):
        roh = {"vorschlaege": [json.loads(_antwort(name=f"n{i}"))["vorschlaege"][0] for i in range(6)]}
        self.assertEqual(len(bl.vorschlaege_lesen(json.dumps(roh), Z)), 3)


class Annehmen(_Lern):
    async def _vorschlag(self, db, user="ua"):
        await self._an(db) if user == "ua" else None
        gespeichert = await bl.vorschlaege_speichern(db, user, "a1", bl.vorschlaege_lesen(_antwort(), Z))
        await db.commit()
        return gespeichert[0]

    async def test_ohne_ja_entsteht_keine_regel(self):
        async with self.Session() as db:
            await self._vorschlag(db)
            self.assertEqual(await bm.regeln_laden(db, "ua"), [])

    async def test_annehmen_legt_die_regel_an(self):
        async with self.Session() as db:
            v = await self._vorschlag(db)
            r = await cu.lernen_vorschlag_annehmen(v.id, user=A, db=db)
            regeln = await bm.regeln_laden(db, "ua")
        self.assertEqual(len(regeln), 1)
        self.assertEqual(regeln[0].von, "agent:a1")
        self.assertEqual(regeln[0].agent_id, "a1")
        self.assertEqual(r["vorschlag"]["status"], "angenommen")
        self.assertEqual(r["vorschlag"]["regel_id"], regeln[0].id)
        self.konfig_mock.assert_awaited_with("ua")

    async def test_zweimal_annehmen_legt_nicht_zweimal_an(self):
        async with self.Session() as db:
            v = await self._vorschlag(db)
            await cu.lernen_vorschlag_annehmen(v.id, user=A, db=db)
            with self.assertRaises(HTTPException) as cm:
                await cu.lernen_vorschlag_annehmen(v.id, user=A, db=db)
            self.assertEqual(cm.exception.status_code, 404)
            self.assertEqual(len(await bm.regeln_laden(db, "ua")), 1)

    async def test_fremder_vorschlag_ist_unantastbar(self):
        async with self.Session() as db:
            v = await self._vorschlag(db)
            for aufruf in (cu.lernen_vorschlag_annehmen, cu.lernen_vorschlag_ablehnen):
                with self.assertRaises(HTTPException) as cm:
                    await aufruf(v.id, user=B, db=db)
                self.assertEqual(cm.exception.status_code, 404)
            self.assertEqual(await bm.regeln_laden(db, "ua"), [])
            self.assertEqual((await bl.vorschlag_holen(db, "ua", v.id)).status, "offen")

    async def test_ablehnen_merkt_sich_und_schlaegt_nicht_wieder_vor(self):
        async with self.Session() as db:
            v = await self._vorschlag(db)
            await cu.lernen_vorschlag_ablehnen(v.id, user=A, db=db)
            self.assertEqual(await bm.regeln_laden(db, "ua"), [])
            nochmal = await bl.vorschlaege_speichern(db, "ua", "a1", bl.vorschlaege_lesen(_antwort(), Z))
        self.assertEqual(nochmal, [])

    async def test_vorhandene_regel_wird_nicht_nochmal_vorgeschlagen(self):
        async with self.Session() as db:
            await self._an(db)
            await bm.regel_anlegen(db, "ua", {"wenn_app": "Chrome", "wenn_titel": "YouTube Studio",
                                              "angebot": "x", "auftrag": "y"})
            await db.commit()
            self.assertEqual(await bl.vorschlaege_speichern(
                db, "ua", "a1", bl.vorschlaege_lesen(_antwort(), Z)), [])

    async def test_zu_viele_regeln_geben_422(self):
        async with self.Session() as db:
            v = await self._vorschlag(db)
            with patch.object(bm, "MAX_REGELN_JE_NUTZER", 0):
                with self.assertRaises(HTTPException) as cm:
                    await cu.lernen_vorschlag_annehmen(v.id, user=A, db=db)
            self.assertEqual(cm.exception.status_code, 422)
            self.assertEqual((await bl.vorschlag_holen(db, "ua", v.id)).status, "offen")


class Schalter(_Lern):
    async def test_standard_ist_aus(self):
        async with self.Session() as db:
            self.assertFalse((await cu.lernen_stand(user=A, db=db))["aktiv"])

    async def test_einschalten_nur_mit_eigenem_agenten(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as cm:
                await cu.lernen_schalten(cu.LernenEingabe(aktiv=True, agent_id="b1"), user=A, db=db)
            self.assertEqual(cm.exception.status_code, 404)
            await cu.lernen_schalten(cu.LernenEingabe(aktiv=True, agent_id="a1"), user=A, db=db)
            s = await cu.lernen_stand(user=A, db=db)
        self.assertTrue(s["aktiv"])
        self.assertEqual(s["agent_id"], "a1")

    async def test_observer_config_nennt_lernen_nur_mit_schalter(self):
        for aktiv in (False, True):
            async with self.Session() as db:
                await bl.schalter_setzen(db, "ua", aktiv)
                await db.commit()
            gesendet = []
            session = {"user_id": "ua", "bridge_connected": True, "allowed_capabilities": {cu.OBSERVE_CAP}}
            with patch.dict(cu._sessions, {"s1": session}), \
                    patch.object(cu, "_an_bridge", AsyncMock(side_effect=lambda s, m: gesendet.append(m))), \
                    patch.object(cu, "_regeln_fuer_bridge", AsyncMock(return_value=[])):
                await cu._beobachter_konfig_senden("s1")
            self.assertIs(gesendet[-1]["lernen"], aktiv)

    async def test_observer_config_ohne_mitsehen_nie_lernen(self):
        async with self.Session() as db:
            await bl.schalter_setzen(db, "ua", True)
            await db.commit()
        gesendet = []
        session = {"user_id": "ua", "bridge_connected": True, "allowed_capabilities": set()}
        with patch.dict(cu._sessions, {"s1": session}), \
                patch.object(cu, "_an_bridge", AsyncMock(side_effect=lambda s, m: gesendet.append(m))):
            await cu._beobachter_konfig_senden("s1")
        self.assertIs(gesendet[-1]["lernen"], False)


class Dienst(_Lern):
    def _dienst(self):
        from app.services.bridge_lernen_service import BridgeLernenDienst
        return BridgeLernenDienst(self.Session, redis=object())

    async def _bereit(self, db):
        from app.models.agent import Agent
        await self._an(db)
        await self._gewohnheit(db)
        a = await db.get(Agent, "a1")
        a.config = {"agent_version": "1.429.0"}
        await db.commit()

    async def test_auswertung_speichert_vorschlaege_und_fragt_im_chat(self):
        async with self.Session() as db:
            await self._bereit(db)
        chat = AsyncMock()
        with patch("app.services.agent_chat_bridge.ask_agent_via_chat", AsyncMock(return_value=_antwort())) as ask, \
                patch("app.services.bridge_lernen_service._in_den_chat", chat):
            r = await self._dienst().auswerten("ua", JETZT)
        self.assertEqual(r, {"status": "ok", "vorschlaege": 1})
        self.assertTrue(ask.await_args.kwargs["nur_ansehen"])
        chat.assert_awaited_once()
        self.assertIn("Ohne dein Ja lege ich nichts an", chat.await_args.args[1])
        async with self.Session() as db:
            self.assertEqual(len(await bl.vorschlaege_laden(db, "ua", nur_offene=True)), 1)
            self.assertEqual(await bm.regeln_laden(db, "ua"), [])
            self.assertEqual((await bl.schalter_holen(db, "ua")).ausgewertet_at.replace(tzinfo=timezone.utc), JETZT)

    async def test_ohne_schalter_wird_nichts_an_den_agenten_gegeben(self):
        async with self.Session() as db:
            await self._bereit(db)
            await bl.schalter_setzen(db, "ua", False)
            await db.commit()
        with patch("app.services.agent_chat_bridge.ask_agent_via_chat", AsyncMock()) as ask:
            r = await self._dienst().auswerten("ua", JETZT)
        self.assertEqual(r["status"], "aus")
        ask.assert_not_awaited()

    async def test_zu_wenig_daten_fragen_den_agenten_nicht(self):
        async with self.Session() as db:
            await self._an(db)
        with patch("app.services.agent_chat_bridge.ask_agent_via_chat", AsyncMock()) as ask:
            r = await self._dienst().auswerten("ua", JETZT)
        self.assertEqual(r["status"], "zu_wenig")
        ask.assert_not_awaited()

    async def test_agent_ohne_nur_ansehen_wird_nicht_gefragt(self):
        async with self.Session() as db:
            await self._bereit(db)
            from app.models.agent import Agent
            (await db.get(Agent, "a1")).config = {"agent_version": "1.300.0"}
            await db.commit()
        with patch("app.services.agent_chat_bridge.ask_agent_via_chat", AsyncMock()) as ask:
            r = await self._dienst().auswerten("ua", JETZT)
        self.assertEqual(r["status"], "kein_agent")
        ask.assert_not_awaited()

    async def test_fehler_des_agenten_markiert_nicht_als_ausgewertet(self):
        async with self.Session() as db:
            await self._bereit(db)
        with patch("app.services.agent_chat_bridge.ask_agent_via_chat", AsyncMock(return_value="[Fehler: Timeout]")):
            r = await self._dienst().auswerten("ua", JETZT)
        self.assertEqual(r["status"], "fehler")
        async with self.Session() as db:
            self.assertIsNone((await bl.schalter_holen(db, "ua")).ausgewertet_at)

    async def test_einmal_am_tag_und_nicht_stuendlich_bei_fehler(self):
        async with self.Session() as db:
            await self._bereit(db)
        dienst = self._dienst()
        ask = AsyncMock(return_value="[Fehler: Timeout]")
        with patch("app.services.agent_chat_bridge.ask_agent_via_chat", ask):
            await dienst.einmal(JETZT)
            await dienst.einmal(JETZT)
        self.assertEqual(ask.await_count, 1)

    async def test_schon_ausgewertet_heute_wird_uebersprungen(self):
        async with self.Session() as db:
            await self._bereit(db)
            (await bl.schalter_holen(db, "ua")).ausgewertet_at = JETZT - timedelta(hours=2)
            await db.commit()
        ask = AsyncMock(return_value=_antwort())
        with patch("app.services.agent_chat_bridge.ask_agent_via_chat", ask):
            self.assertEqual(await self._dienst().einmal(JETZT), 0)
        ask.assert_not_awaited()


class Gegenlese_Haertung(_Lern):
    """Befunde des Gegenlesers (#1023): jeder Test scheitert, wenn die Wache fehlt."""

    def test_zu_kurze_bedingung_ist_nicht_begruendet(self):
        self.assertEqual(bl.vorschlaege_lesen(_antwort(wenn_app="e", wenn_titel=""), Z), [])
        self.assertEqual(bl.vorschlaege_lesen(_antwort(wenn_app="", wenn_titel="a"), Z), [])

    def test_app_und_titel_muessen_zum_selben_fenster_gehoeren(self):
        z = {"apps": [], "fenster": [
            {"app": "Chrome", "titel": "Kalender", "besuche": 5, "tage": 3, "minuten": 1},
            {"app": "Excel", "titel": "Budget", "besuche": 5, "tage": 3, "minuten": 1}]}
        self.assertEqual(bl.vorschlaege_lesen(_antwort(wenn_app="Chrome", wenn_titel="Budget"), z), [])
        self.assertEqual(len(bl.vorschlaege_lesen(_antwort(wenn_app="Excel", wenn_titel="Budget"), z)), 1)

    def test_rueckfrage_enthaelt_keine_links_oder_formatierung(self):
        v = SimpleNamespace(name="[Klick](https://example.invalid)", angebot="**Hi** <b>x</b>",
                            auftrag="`rm`", begruendung="_a_ | b")
        text = bl.rueckfrage_text([v])
        for zeichen in "[]()<>`|":
            self.assertNotIn(zeichen, text.replace("„Aus meiner Arbeit lernen“", ""), zeichen)

    def test_steuerzeichen_fliegen_raus(self):
        self.assertEqual(bl._sauber("a\x00b\u202ec\x07d", 50), "a b c d")

    async def test_voll_verdraengt_das_aelteste_statt_still_aufzuhoeren(self):
        async with self.Session() as db:
            await self._an(db)
            with patch.object(bl, "MAX_ZEILEN", 5), patch.object(bl, "VERDRAENGEN_ZEILEN", 2):
                for i in range(6):
                    z = await bl.protokoll_aufnehmen(db, "ua", "Chrome", f"Seite {i}", 60,
                                                     jetzt=JETZT + timedelta(minutes=i))
                    self.assertIsNotNone(z)
                    await db.commit()
            titel = {e["titel"] for e in await bl.protokoll_lesen(db, "ua")}
        self.assertEqual(titel, {"Seite 2", "Seite 3", "Seite 4", "Seite 5"})

    async def test_ausschalten_und_loeschen_nehmen_die_vorschlaege_mit(self):
        async with self.Session() as db:
            await self._an(db)
            gespeichert = await bl.vorschlaege_speichern(db, "ua", "a1", bl.vorschlaege_lesen(_antwort(), Z))
            await db.commit()
            await cu.lernen_vorschlag_annehmen(gespeichert[0].id, user=A, db=db)
            rest = await bl.vorschlaege_speichern(db, "ua", "a1", bl.vorschlaege_lesen(
                _antwort(wenn_titel="", wenn_app="Google Chrome", name="App"), Z))
            await db.commit()
            self.assertEqual(len(rest), 1)
            await cu.lernen_schalten(cu.LernenEingabe(aktiv=False), user=A, db=db)
            alle = await bl.vorschlaege_laden(db, "ua")
        self.assertEqual([(v.status, v.begruendung) for v in alle], [("angenommen", "")])

    async def test_aufraeumen_entfernt_alte_vorschlaege_aber_keine_angenommenen(self):
        async with self.Session() as db:
            await self._an(db)
            for n, st in (("alt-offen", "offen"), ("alt-ja", "angenommen")):
                db.add(BridgeVorschlag(user_id="ua", name=n, wenn_app="Chrome", wenn_titel=n, angebot="x",
                                       auftrag="y", begruendung="z", status=st,
                                       created_at=JETZT - timedelta(days=40)))
            await db.commit()
            await bl.protokoll_aufraeumen(db, JETZT)
            await db.commit()
            namen = [v.name for v in await bl.vorschlaege_laden(db, "ua")]
        self.assertEqual(namen, ["alt-ja"])

    async def test_gleichzeitiges_annehmen_ergibt_eine_regel(self):
        async with self.Session() as db:
            await self._an(db)
            v = (await bl.vorschlaege_speichern(db, "ua", "a1", bl.vorschlaege_lesen(_antwort(), Z)))[0]
            await db.commit()
            vid = v.id
        async with self.Session() as d1, self.Session() as d2:
            # d2 hat den Vorschlag noch als „offen“ geladen, während d1 schon angenommen hat.
            v2 = await bl.vorschlag_holen(d2, "ua", vid)
            self.assertEqual(v2.status, "offen")
            await bl.vorschlag_annehmen(d1, "ua", vid)
            await d1.commit()
            self.assertIsNone(await bl.vorschlag_annehmen(d2, "ua", vid))
            await d2.commit()
        async with self.Session() as db:
            self.assertEqual(len(await bm.regeln_laden(db, "ua")), 1)

    async def test_gestoppt_bucht_nichts(self):
        async with self.Session() as db:
            await self._an(db)
        session = {"user_id": "ua", "agent_id": "a1", "beobachten": True, "gestoppt": True}
        with patch.object(cu, "_beobachten_erlaubt", return_value=True):
            await cu._nutzung_buchen(session, {"app": "Chrome", "titel": "Seite", "dauer_sekunden": 60})
            session["gestoppt"] = False
            await cu._nutzung_buchen(session, {"app": "Chrome", "titel": "Seite", "dauer_sekunden": 60})
        async with self.Session() as db:
            self.assertEqual(await bl.protokoll_anzahl(db, "ua"), 1)

    async def test_zweite_auswertung_gleichzeitig_gibt_409(self):
        cu._AUSWERTUNG_LAEUFT.add("ua")
        try:
            with self.assertRaises(HTTPException) as cm:
                await cu.lernen_auswerten(user=A)
            self.assertEqual(cm.exception.status_code, 409)
        finally:
            cu._AUSWERTUNG_LAEUFT.discard("ua")


if __name__ == "__main__":
    unittest.main()
