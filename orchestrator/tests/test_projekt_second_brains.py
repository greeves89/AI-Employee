"""Second Brains an ein Projekt anbinden.

Die Projektsuche (Oberfläche und Werkzeug ``projekt_wissen_suchen`` aller
Laufzeiten) durchsucht dann auch die angebundenen Brains, jeder Treffer nennt
seine Quelle; gelesen wird nur lesend und nur aus angebundenen Brains. Anbinden
darf man nur, was man selbst nutzen darf — und wird dem Ersteller ein Brain
entzogen, fällt es aus dem Projekt heraus.
"""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api import teams as api
from app.core import projekt_wissen
from app.models.second_brain import SecondBrain
from app.models.team import Team
from app.dependencies import AgentPrincipal
from app.models.user import UserRole
from tests.test_projekte import MITGLIED, Basis

#: Nutzer B, dem der Ersteller (A) das Projekt freigegeben hat.
GETEILT = SimpleNamespace(id="ub", role=UserRole.MEMBER)


class AngebundeneBrains(Basis):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.freigabe = {"brain-vertrieb": "ro", "brain-technik": None}
        #: Abweichende Freigaben einzelner Nutzer (Rest: ``self.freigabe``).
        self.freigaben: dict = {}
        p = patch("app.core.mounts.freigegebene_mounts",
                  new=AsyncMock(side_effect=lambda user, db: self.freigaben.get(str(user.id), self.freigabe)))
        p.start()
        self.addCleanup(p.stop)
        # Noch nichts eingelesen (wie ein frisch angelegtes Brain): Suche über die Ablage selbst.
        ohne_index = patch("app.services.vault_search._has_chunks", new=AsyncMock(return_value=False))
        ohne_index.start()
        self.addCleanup(ohne_index.stop)
        async with self.Session() as db:
            for label, slug, name, text in (
                ("brain-vertrieb", "vertrieb", "Vertrieb", "# Preise\nDer Stundensatz für Analysen liegt bei 140 Euro."),
                ("brain-technik", "technik", "Technik", "# Server\nDer Testserver heißt ablage-test."),
                ("brain-geheim", "geheim", "Geheim", "# Gehälter\nNicht für Projekte."),
            ):
                pfad = os.path.join(self.tmp.name, slug)
                os.makedirs(pfad)
                with open(os.path.join(pfad, "index.md"), "w") as fh:
                    fh.write(text)
                db.add(SecondBrain(label=label, name=name, slug=slug, host_path=pfad,
                                   container_path=f"/mnt/brains/{slug}", default_mode="rw", is_active=True))
            await db.commit()

    async def _anbinden(self, labels):
        async with self.Session() as db:
            return await api.update_team("p1", api.UpdateTeam(angebundene_brains=labels), user=MITGLIED, db=db)

    async def test_anbinden_nur_was_man_selbst_nutzen_darf(self):
        r = await self._anbinden(["brain-vertrieb", "brain-technik", "brain-vertrieb"])
        self.assertEqual(r["angebundene_brains"], ["brain-vertrieb", "brain-technik"])
        for falsch in (["brain-geheim"], ["gibt-es-nicht"], [f"b{i}" for i in range(11)]):
            with self.subTest(falsch=falsch), self.assertRaises(HTTPException) as e:
                await self._anbinden(falsch)
            self.assertEqual(e.exception.status_code, 422)
        # Ein fremdes Brain verrät sich nicht: gleiche Meldung wie „gibt es nicht“.
        with self.assertRaises(HTTPException) as e:
            await self._anbinden(["brain-geheim"])
        self.assertIn("gibt es nicht", e.exception.detail)
        r = await self._anbinden([])
        self.assertEqual(r["angebundene_brains"], [])

    async def test_suche_findet_auch_im_angebundenen_brain_mit_quelle(self):
        await self._anbinden(["brain-vertrieb"])
        async with self.Session() as db:
            r = await api.wissen_suchen("p1", "Stundensatz", user=MITGLIED, db=db)
        aus_brain = [t for t in r["treffer"] if t["brain"] == "brain-vertrieb"]
        self.assertTrue(aus_brain)
        self.assertEqual(aus_brain[0]["quelle"], "Vertrieb")
        self.assertFalse([t for t in r["treffer"] if t["brain"] == "brain-technik"])   # nicht angebunden

    async def test_lesen_nur_aus_angebundenen_und_eingesperrt(self):
        await self._anbinden(["brain-technik"])
        async with self.Session() as db:
            r = await api.wissen_lesen("p1", "index.md", brain="brain-technik", user=MITGLIED, db=db)
            self.assertIn("ablage-test", r["inhalt"])
            with self.assertRaises(HTTPException) as e:
                await api.wissen_lesen("p1", "index.md", brain="brain-geheim", user=MITGLIED, db=db)
            self.assertEqual(e.exception.status_code, 404)
            with self.assertRaises(HTTPException) as e:
                await api.wissen_lesen("p1", "../vertrieb/index.md", brain="brain-technik", user=MITGLIED, db=db)
            self.assertEqual(e.exception.status_code, 400)

    async def test_entzogene_freigabe_nimmt_das_brain_aus_dem_projekt(self):
        await self._anbinden(["brain-vertrieb"])
        self.freigabe = {}
        async with self.Session() as db:
            r = await api.wissen_suchen("p1", "Stundensatz", user=MITGLIED, db=db)
            self.assertFalse([t for t in r["treffer"] if t["brain"]])
            with self.assertRaises(HTTPException) as e:
                await api.wissen_lesen("p1", "index.md", brain="brain-vertrieb", user=MITGLIED, db=db)
        self.assertEqual(e.exception.status_code, 404)

    async def test_auftrag_nennt_die_angebundenen_brains(self):
        await self._anbinden(["brain-technik", "brain-vertrieb"])
        async with self.Session() as db:
            text = await projekt_wissen.auszug(db, await db.get(Team, "p1"))
        self.assertIn("Angebundene Second Brains", text)
        self.assertIn("Technik, Vertrieb", text)

    async def test_projektwissen_eines_anderen_projekts_ist_nie_anbindbar(self):
        """Auch nicht fuer Administratoren (Freigabe ``None`` = alle Brains)."""
        async with self.Session() as db:
            db.add(SecondBrain(label="brain-projekt-p2", name="Anderes", slug="projekt-p2",
                               host_path=self.tmp.name, container_path="/mnt/brains/p2", default_mode="rw",
                               is_active=True))
            (await db.get(Team, "p2")).wissen_slug = "projekt-p2"
            await db.commit()
        self.freigabe = None
        with self.assertRaises(HTTPException) as e:
            await self._anbinden(["brain-projekt-p2"])
        self.assertEqual(e.exception.status_code, 422)
        # Altbestand (schon eingetragen) wirkt ebenfalls nicht.
        async with self.Session() as db:
            (await db.get(Team, "p1")).angebundene_brains = ["brain-projekt-p2"]
            await db.commit()
            with self.assertRaises(HTTPException) as e:
                await api.wissen_lesen("p1", "index.md", brain="brain-projekt-p2", user=MITGLIED, db=db)
        self.assertEqual(e.exception.status_code, 404)

    async def test_geteiltes_projekt_gibt_keine_brain_freigaben_weiter(self):
        await self._anbinden(["brain-vertrieb", "brain-technik"])
        async with self.Session() as db:
            (await db.get(Team, "p1")).geteilt_mit = ["ub"]
            await db.commit()
        self.freigaben["ub"] = {"brain-technik": "ro"}
        async with self.Session() as db:
            r = await api.wissen_suchen("p1", "Stundensatz", user=GETEILT, db=db)
            self.assertFalse([t for t in r["treffer"] if t["brain"] == "brain-vertrieb"])
            with self.assertRaises(HTTPException) as e:
                await api.wissen_lesen("p1", "index.md", brain="brain-vertrieb", user=GETEILT, db=db)
            self.assertEqual(e.exception.status_code, 404)
            r = await api.wissen_lesen("p1", "index.md", brain="brain-technik", user=GETEILT, db=db)
            self.assertIn("ablage-test", r["inhalt"])

    async def test_agent_liest_mit_den_rechten_seines_besitzers(self):
        await self._anbinden(["brain-vertrieb"])
        self.freigaben["ub"] = {}
        async with self.Session() as db:
            (await db.get(Team, "p1")).member_agent_ids = ["a1", "b1"]
            await db.commit()
            # a1 gehoert dem Ersteller, b1 dem Nutzer B ohne Freigabe.
            r = await api.wissen_lesen("p1", "index.md", brain="brain-vertrieb", user=AgentPrincipal(id="a1"), db=db)
            self.assertIn("140 Euro", r["inhalt"])
            with self.assertRaises(HTTPException) as e:
                await api.wissen_lesen("p1", "index.md", brain="brain-vertrieb", user=AgentPrincipal(id="b1"), db=db)
            self.assertEqual(e.exception.status_code, 404)

    async def test_entzogenes_brain_blockiert_das_speichern_nicht(self):
        await self._anbinden(["brain-vertrieb"])
        self.freigabe = {"brain-technik": None}
        r = await self._anbinden(["brain-vertrieb", "brain-technik"])
        self.assertEqual(r["angebundene_brains"], ["brain-vertrieb", "brain-technik"])
