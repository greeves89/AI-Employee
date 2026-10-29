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
            # In den Eingang: gearbeitet wird nur an Bereitem, einplanen ist Sache des Leads/Menschen.
            self.assertTrue(all(k.spalte == "eingang" and k.ersteller_user_id is None for k in karten))
            letzte = (await projekt_gespraech.liste(db, "p1"))[-1]
            self.assertEqual(letzte.art, "protokoll")
            brain = await projekt_wissen.brain_des_projekts(db, await db.get(Team, "p1"))
            seiten = [e["path"] for e in projekt_wissen.vault.list_entries(brain.host_path)]
            self.assertTrue(any(s.startswith("protokolle/") for s in seiten))


class BesprechungKenntDenStand(_Arbeitsraum):
    """Ein Agent brachte jeden Morgen ein längst geschlossenes Issue ins Daily, und
    jeder Punkt der Zusammenfassung wurde zur Karte (19.–28.10.2026). Jetzt sieht
    die Besprechung den Stand des Boards, und erledigte oder doppelte Punkte werden
    keine Karten mehr."""

    async def _karten_anlegen(self):
        from app.core import projekte

        async with self.Session() as db:
            erledigt = projekte.neue_karte("p1", "Takt-Sperre für Sekretariats-Zeitpläne", quelle="issue",
                                           extern_nr=961, von="github:o/r")
            erledigt.spalte, erledigt.erledigt_at = "erledigt", datetime.now(timezone.utc) - timedelta(days=2)
            db.add_all([
                erledigt,
                projekte.neue_karte("p1", "Login-Fehler nachstellen", spalte="bereit"),
                projekte.neue_karte("p1", "Alte Idee aus dem Eingang", spalte="eingang"),
            ])
            await db.commit()

    async def _abschluss(self, zusammenfassung):
        @contextlib.asynccontextmanager
        async def _sitzung():
            async with self.Session() as db:
                yield db

        with patch("app.db.session.async_session_factory", _sitzung):
            await projekt_termine.abschluss("raum1", "p1", "Daily: Projekt", zusammenfassung, ["a1"])
        async with self.Session() as db:
            from sqlalchemy import select

            neu = (await db.execute(select(ProjektKarte).where(
                ProjektKarte.team_id == "p1", ProjektKarte.notiz == "Aus Besprechung"))).scalars().all()
            letzte = (await projekt_gespraech.liste(db, "p1"))[-1]
        return sorted(k.titel for k in neu), letzte.text

    async def test_stand_zeigt_offenes_und_erledigtes_aber_nicht_den_eingang(self):
        await self._karten_anlegen()
        async with self.Session() as db:
            stand = await projekt_termine.stand_jetzt(db, "p1")
        self.assertIn("Login-Fehler nachstellen", stand)
        self.assertIn("#961", stand)
        self.assertIn("Erledigt", stand)
        self.assertNotIn("Alte Idee aus dem Eingang", stand)   # nur als Kennzahl
        self.assertIn("Inbox 1", stand)

    async def test_erledigte_und_doppelte_punkte_werden_keine_karten(self):
        await self._karten_anlegen()
        titel, text = await self._abschluss(
            "## Action Items\n"
            "- [ ] DevBot: Für #961 die AST-Inventur am aktuellen Commit wiederholen\n"
            "- [ ] Login-Fehler nachstellen\n"
            "- [ ] Neu: Ladezeit der Projektseite messen\n"
            "- [ ] Neu: Ladezeit der Projektseite messen\n"
            "- [ ] #961 und #1063 zusammen prüfen\n")
        # #961 ist geschlossen; „Login-Fehler …“ steht schon in Bereit; der doppelte
        # Punkt kommt einmal; wer zusätzlich ein offenes Issue nennt, bleibt.
        self.assertEqual(titel, ["#961 und #1063 zusammen prüfen", "Neu: Ladezeit der Projektseite messen"])
        self.assertIn("3 Punkt(e) übersprungen", text)

    async def test_umformulierter_punkt_zu_erledigtem_wird_keine_karte(self):
        """26.–28.10.: Jeden Morgen dieselben #961-Punkte, anders formuliert, ohne Nummer."""
        from app.core import projekte

        async with self.Session() as db:
            alt = projekte.neue_karte("p1", "DevBot: AST-Wache für alle Erzeuger der Zeitplan-Präfixe ergänzen",
                                      spalte="eingang", von="besprechung:gestern")
            alt.spalte = "erledigt"
            alt.erledigt_at = datetime.now(timezone.utc) - timedelta(days=1)
            db.add(alt)
            await db.commit()
        titel, _ = await self._abschluss(
            "## Action Items\n"
            "- [ ] DevBot – heute vor Review: AST-Wache für Erzeuger der Zeitplan-Präfixe ergänzen\n"
            "- [ ] Ladezeit der Projektseite messen und dokumentieren\n")
        self.assertEqual(titel, ["Ladezeit der Projektseite messen und dokumentieren"])

    async def test_hoechstens_fuenf_karten_je_besprechung(self):
        themen = ["Ladezeit messen", "Rechnungen prüfen", "Handbuch kürzen", "Logos tauschen", "Backup testen",
                  "Termine planen", "Kunden anrufen", "Server härten", "Preise prüfen"]
        punkte = "".join(f"- [ ] {t} und Ergebnis notieren\n" for t in themen)
        titel, text = await self._abschluss("## Action Items\n" + punkte)
        self.assertEqual(len(titel), projekt_termine.MAX_KARTEN)
        self.assertIn("4 Punkt(e) übersprungen", text)

    async def test_ohne_projekt_kein_stand(self):
        async with self.Session() as db:
            self.assertEqual(await projekt_termine.stand_jetzt(db, "gibt-es-nicht"), "")


class StatusAnDerQuelle(_Arbeitsraum):
    """#1106: Ein Punkt zu einem geschlossenen Issue, zu dem KEINE Karte (mehr) liegt,
    wurde bei jedem Protokoll neu angelegt — die Wache las die Board-Spalte „erledigt“
    statt den Status beim Hoster. Gemessen am 28.10.2026 kannte sie dadurch 4 Nummern,
    während im Repo hunderte Issues geschlossen waren."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.Session() as db:
            (await db.get(Team, "p1")).repo = "o/r"
            await db.commit()

    ITEM = "Für #961 die Inventur wiederholen"

    async def _abschluss(self, zustaende, *, redis="redis-attrappe", kaputt=False, prs=(), punkte=None,
                         langsam=0.0, karten=(), zugang_langsam=0.0):
        """``zustaende``: Issue-Nummer → Status beim Hoster (fehlende Nummer = nicht
        lesbar, also ``None`` — so melden die Anbieter 404/401/403 und eine erschöpfte
        Abfragegrenze). ``prs``: Nummern, die beim Hoster ein Pull Request sind.
        ``kaputt``: Quelle wirft. ``karten``: Titel, die vorher schon auf dem Board liegen.

        Die Attrappe spiegelt ``get_issue`` — EINE Abfrage je Nummer, und das Feld
        ``pull_request`` steht in derselben Antwort (so antworten GitHub und Forgejo)."""
        gefragt = []
        if karten:
            from app.core import projekte

            async with self.Session() as db:
                for titel in karten:
                    db.add(projekte.neue_karte("p1", titel, spalte="bereit"))
                await db.commit()

        class _Anbieter:
            async def get_issue(self, client, token, repo, nummer):
                gefragt.append((repo, nummer))
                if kaputt:
                    raise RuntimeError("Hoster antwortet nicht")
                # ``langsam`` entweder als Zahl (gilt fuer alle) oder je Nummer —
                # nur so laesst sich eine Abfrage messen, die VOR der haengenden liegt.
                warte = langsam.get(nummer, 0.0) if isinstance(langsam, dict) else langsam
                if warte:
                    await asyncio.sleep(warte)
                if nummer not in zustaende:
                    return None                     # nicht lesbar (404/401/403/Grenze)
                antwort = {"number": nummer, "state": zustaende[nummer]}
                if nummer in prs:
                    antwort["pull_request"] = {"url": "https://example.invalid/pulls/1"}
                return antwort

        @contextlib.asynccontextmanager
        async def _sitzung():
            async with self.Session() as db:
                yield db

        async def _zugang(db, r, team):
            if zugang_langsam:
                await asyncio.sleep(zugang_langsam)
            return "tok", _Anbieter()

        with patch("app.db.session.async_session_factory", _sitzung), \
                patch("app.core.projekt_git.zugang", _zugang):
            text = "## Action Items\n" + "".join(f"- [ ] {p}\n" for p in (punkte or [self.ITEM]))
            await projekt_termine.abschluss("raum1", "p1", "Daily: Projekt", text, ["a1"], redis=redis)
        async with self.Session() as db:
            from sqlalchemy import select

            neu = (await db.execute(select(ProjektKarte).where(
                ProjektKarte.team_id == "p1", ProjektKarte.notiz == "Aus Besprechung"))).scalars().all()
        return [k.titel for k in neu], gefragt

    async def test_geschlossenes_issue_ohne_karte_wird_keine_karte(self):
        titel, gefragt = await self._abschluss({961: "closed"})
        self.assertEqual(titel, [])
        self.assertEqual(gefragt, [("o/r", 961)])   # am Repo gemessen, nicht am Board

    async def test_offenes_issue_wird_sehr_wohl_eine_karte(self):
        """Gegenprobe mit derselben Attrappe: antwortet die Quelle „offen“, entsteht die
        Karte — sonst würde der Test oben auch bei einer Wache bestehen, die nichts fragt."""
        titel, gefragt = await self._abschluss({961: "open"})
        self.assertEqual((titel, gefragt), ([self.ITEM], [("o/r", 961)]))

    async def test_quelle_nicht_erreichbar_legt_die_karte_an(self):
        """Bewusst so festgelegt: ein doppelter Punkt ist billiger als ein verlorener."""
        titel, _ = await self._abschluss({961: "closed"}, kaputt=True)
        self.assertEqual(titel, [self.ITEM])

    async def test_ohne_redis_wird_nicht_gefragt(self):
        """Aufrufer ohne ``redis`` (Bestand, Tests): Verhalten wie bisher, kein Absturz."""
        titel, gefragt = await self._abschluss({961: "closed"}, redis=None)
        self.assertEqual((titel, gefragt), ([self.ITEM], []))

    async def test_keine_antwort_der_quelle_legt_die_karte_an(self):
        """Die Anbieter werfen bei 404/401/403 und erschöpfter Abfragegrenze NICHT, sie
        liefern ``None``. Das ist der häufigste Ausfallweg (anonyme Projekte: 60 Abfragen
        je Stunde) und muss wie „nicht geschlossen" wirken, nicht wie „geschlossen"."""
        titel, gefragt = await self._abschluss({})
        self.assertEqual((titel, gefragt), ([self.ITEM], [("o/r", 961)]))

    async def test_pull_request_ist_kein_erledigtes_issue(self):
        """`/issues/{n}` antwortet bei GitHub auch für PRs, und „#N" meint im Protokoll
        oft etwas anderes als ein Issue. Sonst verschwände „PR #1126 nachziehen" lautlos."""
        titel, _ = await self._abschluss({1126: "closed"}, prs=(1126,), punkte=["PR #1126 nachziehen"])
        self.assertEqual(titel, ["PR #1126 nachziehen"])

    async def test_hoechstens_zwanzig_abfragen_je_besprechung(self):
        """Die im CHANGELOG zugesagte Obergrenze — ein Protokoll voller Nummern darf das
        Ende der Besprechung nicht aufhalten."""
        nummern = list(range(500, 530))                      # 30 Nummern in EINEM Punkt
        punkt = "Sammelpunkt " + " ".join(f"#{n}" for n in nummern)
        _, gefragt = await self._abschluss({}, punkte=[punkt])
        self.assertEqual(len(gefragt), projekt_termine.MAX_STATUSABFRAGEN)

    async def test_rollback_beim_zugang_kostet_die_karten_nicht(self):
        """`zugang` kann auf der ÜBERGEBENEN Sitzung ein `rollback` auslösen —
        `oauth_service` tut das, wenn sein `commit` scheitert, und `projekt_git`
        schluckt die Ausnahme danach und liefert normal zurück. Lief die Abfrage auf
        der Sitzung der Besprechung, entwertete das alle geladenen Objekte: das
        Kartenanlegen lud `team` nachträglich nach (MissingGreenlet), und Karten,
        Protokollnachricht und Übersprungen-Spur gingen still verloren."""

        gefragt = []

        class _Anbieter:
            async def get_issue(self, client, token, repo, nummer):
                gefragt.append(nummer)
                return {"number": nummer, "state": "open"}

        async def _zugang(db, r, team):
            await db.rollback()
            return "tok", _Anbieter()

        @contextlib.asynccontextmanager
        async def _sitzung():
            async with self.Session() as db:
                yield db

        with patch("app.db.session.async_session_factory", _sitzung), \
                patch("app.core.projekt_git.zugang", _zugang):
            await projekt_termine.abschluss("raum1", "p1", "Daily: Projekt",
                                            f"## Action Items\n- [ ] {self.ITEM}\n", ["a1"],
                                            redis="redis-attrappe")
        async with self.Session() as db:
            from sqlalchemy import select

            karten = (await db.execute(select(ProjektKarte).where(
                ProjektKarte.team_id == "p1", ProjektKarte.notiz == "Aus Besprechung"))).scalars().all()
            nachrichten = [n for n in await projekt_gespraech.liste(db, "p1") if n.art == "protokoll"]
        self.assertEqual([k.titel for k in karten], [self.ITEM])
        self.assertEqual(len(nachrichten), 1)
        # Und die Abfrage hat wirklich stattgefunden: liefe der Test nur durch den
        # Sammel-Fehlerzweig (etwa weil die Attrappe die Schnittstelle verfehlt),
        # waere der Fall "zugang rollt zurueck UND danach wird gemessen" ungedeckt.
        self.assertEqual(gefragt, [961])

    async def test_eine_abfrage_je_nummer(self):
        """Die Zusage „ein Lesevorgang je Nummer". Der frühere Weg (``issue_state`` und
        danach ``pull_state``) brauchte zwei und hatte dadurch den Befund unten."""
        _, gefragt = await self._abschluss({961: "closed"})
        self.assertEqual(gefragt, [("o/r", 961)])

    async def test_nicht_lesbarer_pull_request_wird_nicht_zum_erledigten_issue(self):
        """Der Befund hinter der Umstellung auf ``get_issue``: ``pull_state`` liefert auch
        bei 403/429/5xx ``None``, und ``None`` hieß „kein Pull Request". Eine NICHT LESBARE
        Quelle machte aus der PR-Nummer also ein erledigtes Issue, und „PR #1126 nachziehen"
        verschwand lautlos — genau die Fehlerklasse, die diese Wache vermeiden soll.
        ``get_issue`` liest beides aus EINER Antwort, es gibt keine halb gelesene Lage mehr."""
        titel, gefragt = await self._abschluss({1126: "closed"}, prs=(1126,),
                                               punkte=["PR #1126 nachziehen"])
        self.assertEqual((titel, gefragt), (["PR #1126 nachziehen"], [("o/r", 1126)]))

    async def test_schon_abgedeckter_punkt_fragt_den_hoster_nicht(self):
        """Ein Punkt, den die Wortüberlappung mit einer offenen Karte ohnehin abdeckt,
        wird übersprungen — egal was der Hoster sagt. Dann darf die Wache dafür auch nicht
        fragen: Abfragen sind begrenzt (20) und halten das Ende der Besprechung auf."""
        titel, gefragt = await self._abschluss({961: "open"}, karten=[self.ITEM])
        self.assertEqual((titel, gefragt), ([], []))

    async def test_ein_punkt_deckt_den_naechsten_desselben_protokolls_ab(self):
        """Gegenprobe zur Vorab-Messung: die Dubletten-Erkennung INNERHALB eines
        Protokolls hängt daran, dass die Vergleichsliste in der Schleife mitwächst.
        Zwei Mal fast derselbe Punkt ergibt genau EINE Karte."""
        titel, _ = await self._abschluss(
            {}, punkte=["Für #961 die Inventur wiederholen", "Die Inventur für #961 wiederholen"])
        self.assertEqual(len(titel), 1)

    async def test_eine_nicht_lesbare_nummer_stoppt_die_uebrigen_nicht(self):
        """Eine Nummer, die der Hoster nicht ausliefert, ist ein Einzelfall — sie darf die
        Messung der ANDEREN nicht abbrechen. Ohne die Prüfung „ist das überhaupt eine
        Antwort" liefe die Wache auf ``None.get(...)`` in den Sammel-Fehlerzweig und
        verlöre jede Nummer dahinter, obwohl sie gemessen werden konnte."""
        titel, gefragt = await self._abschluss(
            {970: "closed"}, punkte=["Zu #961 nachfassen", "Für #970 die Inventur abschliessen"])
        self.assertEqual(titel, ["Zu #961 nachfassen"])       # 961 nicht lesbar -> Karte
        self.assertEqual(gefragt, [("o/r", 961), ("o/r", 970)])

    async def test_zugang_liegt_nicht_in_der_frist(self):
        """Das Beschaffen des Zugangs darf die Frist NICHT verbrauchen und erst recht nicht
        von ihr abgebrochen werden: ``zugang`` erneuert bei Bedarf den OAuth-Token (bis zu
        30 s Sperre plus Tausch beim Anbieter). Ein Abbruch mitten im Tausch verbrennt den
        Refresh-Token, der Anbieter hat ihn dann schon gedreht — Fehlerklasse #1013.
        Läge ``zugang`` in der Frist, käme hier keine Abfrage mehr zustande."""
        with patch.object(projekt_termine, "STATUS_FRIST_SEKUNDEN", 0.2):
            titel, gefragt = await self._abschluss({961: "closed"}, zugang_langsam=0.5)
        self.assertEqual((titel, gefragt), ([], [("o/r", 961)]))

    async def test_abbruch_durch_die_frist_behaelt_das_schon_gemessene(self):
        """Die Zusage „bricht die Abfrage nach der Haelfte ab, zaehlt die Haelfte" —
        gefunden vom Gegenleser als EINZIGE Luecke der Batterie: wird im Fehlerzweig
        `set()` statt des schon Gemessenen zurueckgegeben, bleiben alle Tests gruen.

        Lage: #961 antwortet sofort „closed", #970 haengt laenger als die Frist. Danach
        muss #961 gemessen BLEIBEN (kein Karten-Doppel), #970 aber eine Karte werden.
        Raeumt jemand den Fehlerzweig „auf", verliert die Wache bei einem langsamen
        Hoster still die halbe Wirkung von #1106 — ohne dass etwas rot wird."""
        with patch.object(projekt_termine, "STATUS_FRIST_SEKUNDEN", 0.3):
            titel, gefragt = await self._abschluss(
                {961: "closed", 970: "open"}, langsam={970: 5.0},
                punkte=["Zu #961 nachfassen", "Fuer #970 die Inventur abschliessen"])
        self.assertEqual(gefragt, [("o/r", 961), ("o/r", 970)])
        self.assertEqual(titel, ["Fuer #970 die Inventur abschliessen"])

    async def test_frist_bricht_die_abfrage_ab_und_legt_die_karte_an(self):
        """Die zugesagte Gesamtfrist. Eine hängende Quelle darf die Besprechung nicht
        festhalten — und der Punkt muss danach eine Karte werden, nicht verschwinden."""
        with patch.object(projekt_termine, "STATUS_FRIST_SEKUNDEN", 0.05):
            titel, _ = await self._abschluss({961: "closed"}, langsam=0.5)
        self.assertEqual(titel, [self.ITEM])


class BesprechungReichtRedisDurch(unittest.IsolatedAsyncioTestCase):
    """Die Statusabfrage beim Hoster braucht ``redis`` (für den OAuth-Zugang). Ohne
    ``redis`` fragt ``abschluss`` nichts und die Wache fällt stillschweigend auf den
    alten Board-Stand zurück — der Fix von #1106 wäre dann wirkungslos, ohne dass ein
    Test rot wird. Genau die eine Stelle, die ``redis`` weitergibt, wird hier gemessen."""

    async def test_abschluss_bekommt_das_redis_der_besprechung(self):
        from app.api import meeting_rooms

        gesehen = {}

        async def _abschluss(room_id, team_id, raum_name, zusammenfassung, agent_ids, redis=None):
            gesehen["redis"] = redis

        async def _moderator(room_id, agent_id, prompt, redis, timeout_rounds=24):
            return "## Action Items\n- [ ] Etwas tun\n"

        class _Client:
            async def publish(self, *a, **k):
                return 0

        redis = SimpleNamespace(client=_Client())
        room = SimpleNamespace(id="raum1", team_id="p1", name="Daily: Projekt", topic="Stand",
                               agent_ids=["a1"], rounds_completed=1,
                               messages=[{"role": "agent", "agent_id": "a1", "content": "Hallo"}])
        with patch.object(meeting_rooms, "_moderator_request", _moderator), \
                patch.object(projekt_termine, "abschluss", _abschluss):
            await meeting_rooms._generate_todo_summary(room, redis, "mod1")
        self.assertIs(gesehen.get("redis"), redis)


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
