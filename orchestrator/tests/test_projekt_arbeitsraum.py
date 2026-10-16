"""Projekt als Arbeitsraum: Gespräch, Termine, Wissen das mitwächst, aktiver Lead.

Echte (SQLite-)Datenbank und echtes Git der Wissensablage; nur der Versand der
Läufe ist nachgestellt (``_Router`` aus test_projekte).
"""

import asyncio
import contextlib
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.api import teams as api
from app.core import projekt_gespraech, projekt_takt, projekt_termine, projekt_wissen
from app.models.meeting_room import MeetingRoom
from app.models.projekt_karte import ProjektKarte
from app.models.projekt_nachricht import ProjektNachricht
from app.models.schedule import Schedule
from app.models.task import Task, TaskStatus
from app.models.team import Team
from app.models.user import UserRole
from tests.test_projekte import MITGLIED, Basis, _Router, agent


class _Arbeitsraum(Basis):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.engine.begin() as conn:
            for model in (ProjektNachricht, MeetingRoom):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.lead_agent_id = "a1"
            await db.commit()


class Gespraech(_Arbeitsraum):
    async def _schreiben(self, db, text, wer=MITGLIED, router=None, abwarten=True, **kw):
        router = router or _Router(db)
        with patch("app.db.session.async_session_factory", self.Session):
            r = await api.gespraech_schreiben("p1", api.GespraechNachricht(text=text, **kw), router_=router, user=wer,
                                              db=db)
            if abwarten:
                await asyncio.gather(*list(projekt_gespraech._HINTERGRUND))
        return r, router

    async def test_senden_wartet_nicht_aufs_wecken(self):
        """Einen gestoppten Agenten zu starten dauert Sekunden — die Nachricht kommt sofort zurück."""
        frei = asyncio.Event()

        class _Langsam(_Router):
            async def create_and_route_task(self, **kw):
                await frei.wait()
                return await super().create_and_route_task(**kw)

        async with self.Session() as db:
            router = _Langsam(db)
            with patch("app.db.session.async_session_factory", self.Session):
                r, _ = await self._schreiben(db, "@A2 bitte prüfen", router=router, abwarten=False)
                self.assertEqual((r["geweckt"], router.aufrufe), (["a2"], []))
                frei.set()
                await asyncio.gather(*list(projekt_gespraech._HINTERGRUND))
        self.assertEqual(router.aufrufe[0]["agent_id"], "a2")

    async def test_mensch_ohne_ansprache_weckt_den_lead(self):
        async with self.Session() as db:
            r, router = await self._schreiben(db, "Wie ist der Stand?")
        self.assertEqual(r["geweckt"], ["a1"])
        self.assertEqual(router.aufrufe[0]["agent_id"], "a1")
        self.assertIn("Wie ist der Stand?", router.aufrufe[0]["prompt"].split("[EXTERNAL-DATA")[0])

    async def test_ansprache_mit_at_name(self):
        async with self.Session() as db:
            r, router = await self._schreiben(db, "@A2 bitte die Zahlen prüfen")
        self.assertEqual(r["geweckt"], ["a2"])

    async def test_agent_ohne_ansprache_weckt_niemanden(self):
        async with self.Session() as db:
            r, router = await self._schreiben(db, "Ich bin fertig.", wer=agent("a2"))
        self.assertEqual((r["geweckt"], router.aufrufe), ([], []))

    async def test_fremder_agent_wird_nicht_geweckt(self):
        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.member_agent_ids = ["a1", "a2", "b1"]
            await db.commit()
            r, _ = await self._schreiben(db, "@B1 mach mal", wer=MITGLIED)
        self.assertEqual(r["geweckt"], [])

    async def test_entscheidung_landet_im_wissen(self):
        async with self.Session() as db:
            await self._schreiben(db, "Wir liefern nur noch per git aus.", art="entscheidung")
            t = await db.get(Team, "p1")
            brain = await projekt_wissen.brain_des_projekts(db, t)
            from app.core import vault

            self.assertIn("nur noch per git", vault.read_file(brain.host_path, "entscheidungen.md"))

    async def test_antwort_aus_dem_lauf_steht_im_gespraech(self):
        async with self.Session() as db:
            r, router = await self._schreiben(db, "@A2 kurze Frage")
            task = await db.get(Task, router.aufrufe and "neu1")
            task.status, task.result = TaskStatus.COMPLETED, "Die Antwort ist 42."
            task.started_at = datetime.now(timezone.utc) - timedelta(seconds=5)
            await projekt_gespraech.antwort_aus_lauf(db, task)
            await db.commit()
            nachrichten = await projekt_gespraech.liste(db, "p1")
        self.assertEqual((nachrichten[-1].autor_id, nachrichten[-1].text), ("a2", "Die Antwort ist 42."))

    async def test_keine_doppelte_antwort(self):
        async with self.Session() as db:
            _, router = await self._schreiben(db, "@A2 kurze Frage")
            task = await db.get(Task, "neu1")
            task.started_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await self._schreiben(db, "Schon beantwortet.", wer=agent("a2"))
            task.status, task.result = TaskStatus.COMPLETED, "doppelt"
            await projekt_gespraech.antwort_aus_lauf(db, task)
            await db.commit()
            texte = [n.text for n in await projekt_gespraech.liste(db, "p1")]
        self.assertNotIn("doppelt", texte)

    async def test_weckgrenze_je_stunde(self):
        async with self.Session() as db:
            for i in range(projekt_gespraech.MAX_WECKEN_JE_STUNDE):
                db.add(Task(id=f"g{i}", title="Projektgespräch: x", prompt="p", agent_id="a1", team_id="p1"))
            await db.commit()
            r, router = await self._schreiben(db, "@A2 noch eins")
        self.assertEqual(r["geweckt"], [])

    async def test_fremdes_projekt_gesperrt(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as e:
                await api.gespraech_lesen("p2", user=MITGLIED, db=db)
        self.assertEqual(e.exception.status_code, 403)


class Termine(_Arbeitsraum):
    async def test_daily_weekly_und_einmalig(self):
        async with self.Session() as db:
            d = await api.termin_anlegen("p1", api.NeuerTermin(art="daily", uhrzeit="09:00"), user=MITGLIED, db=db)
            w = await api.termin_anlegen("p1", api.NeuerTermin(art="weekly", uhrzeit="15:30", wochentag="Fr"),
                                         user=agent("a1"), db=db)  # der Lead darf
            morgen = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
            e = await api.termin_anlegen("p1", api.NeuerTermin(art="einmalig", uhrzeit="10:00", datum=morgen),
                                         user=MITGLIED, db=db)
            sched = await db.get(Schedule, d["id"])
        self.assertEqual(sched.cron_expression, "0 9 * * 1-5")
        self.assertEqual(sched.team_id, "p1")
        self.assertIn('"team_id": "p1"', sched.prompt)
        self.assertEqual((w["art"], w["uhrzeit"], w["wochentag"]), ("weekly", "15:30", "fr"))
        self.assertEqual(e["art"], "einmalig")
        self.assertIsNotNone(e["naechster"])

    async def test_nur_ersteller_oder_lead(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as e:
                await api.termin_anlegen("p1", api.NeuerTermin(art="daily", uhrzeit="09:00"), user=agent("a2"), db=db)
            self.assertEqual(e.exception.status_code, 403)

    async def test_falsche_angaben(self):
        async with self.Session() as db:
            for termin in (api.NeuerTermin(art="daily", uhrzeit="25:00"),
                           api.NeuerTermin(art="weekly", uhrzeit="09:00", wochentag="xx"),
                           api.NeuerTermin(art="einmalig", uhrzeit="09:00", datum="2020-01-01"),
                           api.NeuerTermin(art="monatlich", uhrzeit="09:00")):
                with self.assertRaises(HTTPException) as e:
                    await api.termin_anlegen("p1", termin, user=MITGLIED, db=db)
                self.assertEqual(e.exception.status_code, 422)

    async def test_liste_und_entfernen(self):
        async with self.Session() as db:
            d = await api.termin_anlegen("p1", api.NeuerTermin(art="daily", uhrzeit="09:00"), user=MITGLIED, db=db)
            self.assertEqual(len((await api.termine_liste("p1", user=MITGLIED, db=db))["termine"]), 1)
            await api.termin_entfernen("p1", d["id"], user=MITGLIED, db=db)
            self.assertEqual((await api.termine_liste("p1", user=MITGLIED, db=db))["termine"], [])

    async def test_abschluss_protokoll_karten_und_gespraech(self):
        @contextlib.asynccontextmanager
        async def _sitzung():
            async with self.Session() as db:
                yield db

        zusammenfassung = "## Action Items\n- [ ] Login-Fehler nachstellen\n- [ ] Doku ergänzen\n\n## Kontext\nalles gut"
        with patch("app.db.session.async_session_factory", _sitzung):
            await projekt_termine.abschluss("raum1", "p1", "Daily: Projekt", zusammenfassung, ["a1", "a2"])
        async with self.Session() as db:
            from sqlalchemy import select

            karten = (await db.execute(select(ProjektKarte).where(ProjektKarte.team_id == "p1"))).scalars().all()
            self.assertEqual(sorted(k.titel for k in karten), ["Doku ergänzen", "Login-Fehler nachstellen"])
            # Ohne Ersteller → beim Vergeben gerahmt (die Items stammen von Agenten).
            self.assertTrue(all(k.spalte == "bereit" and k.ersteller_user_id is None for k in karten))
            letzte = (await projekt_gespraech.liste(db, "p1"))[-1]
            self.assertEqual(letzte.art, "protokoll")
            brain = await projekt_wissen.brain_des_projekts(db, await db.get(Team, "p1"))
            seiten = [e["path"] for e in projekt_wissen.vault.list_entries(brain.host_path)]
            self.assertTrue(any(s.startswith("protokolle/") for s in seiten))


class TerminText(unittest.TestCase):
    def test_lesbar_statt_kuerzel(self):
        self.assertEqual(projekt_termine.beschreibung({"art": "daily", "uhrzeit": "09:00"}), "Daily (Mo–Fr) um 09:00")
        self.assertEqual(projekt_termine.beschreibung({"art": "weekly", "uhrzeit": "07:15", "wochentag": "so"}),
                         "wöchentlich sonntags um 07:15")
        self.assertEqual(projekt_termine.beschreibung({"art": "einmalig", "uhrzeit": "10:00"}, "2026-10-20"),
                         "einmalig am 20.10.2026 um 10:00")


class WissenWaechst(_Arbeitsraum):
    async def test_anhaengen_verlauf_und_zurueckholen(self):
        async with self.Session() as db:
            await api.wissen_ergaenzen("p1", api.WissenAbschnitt(pfad="erkenntnisse.md", abschnitt="## Eins\nerster"),
                                       user=agent("a2"), db=db)
            await api.wissen_ergaenzen("p1", api.WissenAbschnitt(pfad="erkenntnisse.md", abschnitt="## Zwei\nzweiter"),
                                       user=agent("a1"), db=db)
            inhalt = (await api.wissen_lesen("p1", "erkenntnisse.md", user=MITGLIED, db=db))["inhalt"]
            self.assertIn("erster", inhalt)
            self.assertIn("zweiter", inhalt)
            versionen = (await api.wissen_verlauf("p1", "erkenntnisse.md", user=MITGLIED, db=db))["versionen"]
            self.assertEqual(len(versionen), 2)
            self.assertEqual(versionen[0]["wer"], "A1")
            await api.wissen_wiederherstellen("p1", api.Wiederherstellen(pfad="erkenntnisse.md",
                                                                         version=versionen[-1]["version"]),
                                              user=MITGLIED, db=db)
            inhalt = (await api.wissen_lesen("p1", "erkenntnisse.md", user=MITGLIED, db=db))["inhalt"]
            self.assertNotIn("zweiter", inhalt)
            self.assertEqual(len((await api.wissen_verlauf("p1", "erkenntnisse.md", user=MITGLIED, db=db))["versionen"]), 3)

    async def test_git_fuehrt_nichts_aus_der_ablage_aus(self):
        """Ein Hook in der Ablage (wie auch immer er dort hinkäme) läuft beim Festhalten nicht."""
        import os
        import stat

        from app.core import projekt_wissen, vault

        async with self.Session() as db:
            await api.wissen_ergaenzen("p1", api.WissenAbschnitt(pfad="a.md", abschnitt="eins"), user=agent("a1"), db=db)
            brain = await projekt_wissen.brain_des_projekts(db, await db.get(Team, "p1"))
            spur = os.path.join(brain.host_path, "..", "hook-lief")
            for hook in ("pre-commit", "post-commit"):
                pfad = os.path.join(brain.host_path, ".git", "hooks", hook)
                with open(pfad, "w") as f:
                    f.write(f"#!/bin/sh\ntouch {spur}\n")
                os.chmod(pfad, os.stat(pfad).st_mode | stat.S_IEXEC)
            await api.wissen_ergaenzen("p1", api.WissenAbschnitt(pfad="a.md", abschnitt="zwei"), user=agent("a1"), db=db)
            self.assertFalse(os.path.exists(spur))
            self.assertEqual(len((await api.wissen_verlauf("p1", "a.md", user=MITGLIED, db=db))["versionen"]), 2)
            for boese in (".git/hooks/post-commit", "x/../.git/config"):
                with self.assertRaises(ValueError):
                    vault.resolve_path(brain.host_path, boese)

    async def test_abnahme_schreibt_ergebnisse(self):
        async with self.Session() as db:
            await self._karte(db, "k1", "pruefung", task_id="t1", agent_id="a1")
            db.add(Task(id="t1", title="t", prompt="p", agent_id="a1", status=TaskStatus.COMPLETED,
                        result="Drei Ideen: A, B, C", metadata_={"karte_id": "k1"}))
            await db.commit()
            await api.karte_abnehmen("p1", "k1", user=MITGLIED, db=db)
            inhalt = (await api.wissen_lesen("p1", "ergebnisse.md", user=MITGLIED, db=db))["inhalt"]
            self.assertIn("Drei Ideen", inhalt)
            self.assertEqual((await projekt_gespraech.liste(db, "p1"))[-1].art, "ergebnis")

    async def test_wissen_aufbauen_gibt_dem_lead_eine_karte(self):
        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.repo = "o/r"
            await db.commit()
            router = _Router(db)
            with patch("app.api.teams._team_roster", new=lambda *a, **k: _leer()):
                r = await api.wissen_aufbauen("p1", request=None, router_=router, user=MITGLIED, db=db)
        self.assertEqual((r["titel"], r["agent_id"]), ("Projektwissen aufbauen", "a1"))
        self.assertIn("o/r", router.aufrufe[0]["prompt"])


async def _leer():
    return "- A1 (LEAD)"


class LeadTakt(_Arbeitsraum):
    async def test_takt_laeuft_bei_neuer_nachricht(self):
        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.takt_minuten = 60
            await projekt_takt.abgleichen(db, team)
            await db.commit()
            sched = await db.get(Schedule, team.takt_schedule_id)
            sched.last_run_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            with patch("app.core.projekte._zone", return_value=__import__("zoneinfo").ZoneInfo("UTC")), \
                    patch("app.core.projekt_takt.datetime") as uhr:
                uhr.now.return_value = datetime.now(timezone.utc).replace(hour=8)
                grund, _ = await projekt_takt.pruefen(db, sched)
                self.assertIsNotNone(grund)  # nichts zu tun
                await projekt_gespraech.posten(db, team, autor_typ="mensch", autor_id="ua", autor_name="A",
                                               text="Neue Frage")
                await db.commit()
                grund, _ = await projekt_takt.pruefen(db, sched)
        self.assertIsNone(grund)

    def test_auftrag_nennt_die_neuen_aufgaben(self):
        text = projekt_takt.takt_auftrag(SimpleNamespace(name="P", id="p1"))
        for teil in ("projekt_gespraech_lesen", "projekt_termin_anlegen", "projekt_wissen_ergaenzen", "art=bericht"):
            self.assertIn(teil, text)


class Sichtbarkeit(_Arbeitsraum):
    async def test_admin_ohne_freigabe_sieht_das_gespraech_nicht(self):
        admin = SimpleNamespace(id="admin", role=UserRole.ADMIN, email="admin@example.invalid")
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as e:
                await api.gespraech_lesen("p1", user=admin, db=db)
        self.assertEqual(e.exception.status_code, 403)


class ZeitplanOhneProjektRecht(_Arbeitsraum):
    async def test_team_id_im_auftragstext_allein_reicht_nicht(self):
        """Ein selbst geschriebener __meeting__-Zeitplan schiebt nichts in ein fremdes Projekt."""
        import json
        from unittest.mock import AsyncMock, MagicMock

        from app.services.scheduler_service import SchedulerService

        dienst = SchedulerService.__new__(SchedulerService)
        dienst.docker, dienst.redis = None, MagicMock()
        for mit_spalte, erwartet in ((None, None), ("p2", "p2")):
            async with self.Session() as db:
                sched = Schedule(id=f"s{erwartet}", name="x", interval_seconds=0, agent_id=None, enabled=True,
                                 next_run_at=datetime.now(timezone.utc), team_id=mit_spalte,
                                 prompt="__meeting__:" + json.dumps({"name": "n", "agent_ids": ["b1", "a1"], "team_id": "p2"}))
                db.add(sched)
                await db.commit()
                with patch("app.api.meeting_rooms._run_meeting", new=AsyncMock()), \
                        patch("asyncio.create_task", new=lambda c: c.close()):
                    await dienst._execute_meeting_schedule(db, sched)
                from sqlalchemy import select

                raum = (await db.execute(select(MeetingRoom).order_by(MeetingRoom.created_at.desc()))).scalars().first()
                self.assertEqual(raum.team_id, erwartet)
