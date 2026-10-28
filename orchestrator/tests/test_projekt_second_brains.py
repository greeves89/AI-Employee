"""Second Brains an ein Projekt anbinden.

Die Projektsuche (Oberfläche und Werkzeug ``projekt_wissen_suchen`` aller
Laufzeiten) durchsucht dann auch die angebundenen Brains, jeder Treffer nennt
seine Quelle; gelesen wird nur lesend und nur aus angebundenen Brains. Anbinden
darf man nur, was man selbst nutzen darf — und wird dem Ersteller ein Brain
entzogen, fällt es aus dem Projekt heraus.
"""

import os
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api import teams as api
from app.core import projekt_wissen
from app.models.second_brain import SecondBrain
from app.models.team import Team
from tests.test_projekte import MITGLIED, Basis


class AngebundeneBrains(Basis):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.freigabe = {"brain-vertrieb": "ro", "brain-technik": None}
        p = patch("app.core.mounts.freigegebene_mounts", new=AsyncMock(side_effect=lambda user, db: self.freigabe))
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
