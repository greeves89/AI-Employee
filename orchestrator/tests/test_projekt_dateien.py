"""Dateien am Projekt (Wissen → Dateien).

Original zum Herunterladen neben der Text-Ablage, ausgelesener Text als Seite im
Projektwissen (durchsuchbar fuer Agenten). Geprueft ueber die echten Endpunkte:
Auslesen (Word, Excel, Text), Namen und Pfade, Groesse und Art, Herunterladen nur
als Anhang, Loeschen samt Seite, Betrachter und fremde Projekte.
"""

import io
import unittest
import os

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.api import teams as api
from app.core import projekt_dateien, vault
from tests.test_projekte import BETRACHTER, MITGLIED, Basis


def _datei(name: str, inhalt: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(inhalt), filename=name)


def _docx(text: str) -> bytes:
    from docx import Document

    doc, puffer = Document(), io.BytesIO()
    doc.add_paragraph(text)
    doc.save(puffer)
    return puffer.getvalue()


def _xlsx(zeilen: list[list]) -> bytes:
    from openpyxl import Workbook

    wb, puffer = Workbook(), io.BytesIO()
    for z in zeilen:
        wb.active.append(z)
    wb.save(puffer)
    return puffer.getvalue()


class Dateien(Basis):
    async def _hochladen(self, name, inhalt, user=MITGLIED, projekt="p1"):
        async with self.Session() as db:
            return await api.datei_hochladen(projekt, _datei(name, inhalt), user=user, db=db)

    async def _brain(self):
        from app.core import projekt_wissen
        from app.models.team import Team

        async with self.Session() as db:
            return await projekt_wissen.brain_des_projekts(db, await db.get(Team, "p1"))

    async def test_word_und_excel_werden_ausgelesen_und_als_seite_abgelegt(self):
        r = await self._hochladen("Lastenheft.docx", _docx("Die Anlage muss 400 Teile je Stunde schaffen."))
        self.assertEqual((r["name"], r["seite"], r["text_ausgelesen"]),
                         ("Lastenheft.docx", "dateien/Lastenheft.docx.md", True))
        await self._hochladen("Angebot.xlsx", _xlsx([["Position", "Preis"], ["Analyse", 4200]]))
        brain = await self._brain()
        self.assertIn("400 Teile je Stunde", vault.read_file(brain.host_path, "dateien/Lastenheft.docx.md"))
        self.assertIn("Analyse\t4200", vault.read_file(brain.host_path, "dateien/Angebot.xlsx.md"))
        async with self.Session() as db:
            liste = await api.dateien_liste("p1", user=MITGLIED, db=db)
        self.assertEqual([d["name"] for d in liste["dateien"]], ["Angebot.xlsx", "Lastenheft.docx"])
        # Das Original liegt NICHT in der git-Ablage, sondern daneben.
        self.assertFalse(os.path.exists(os.path.join(brain.host_path, "Lastenheft.docx")))
        self.assertTrue(os.path.isfile(brain.host_path + ".dateien/Lastenheft.docx"))

    async def test_namen_werden_bereinigt_und_nicht_ueberschrieben(self):
        r1 = await self._hochladen("../../etc/notiz.txt", b"eins")
        r2 = await self._hochladen("notiz.txt", b"zwei")
        self.assertEqual((r1["name"], r2["name"]), ("notiz.txt", "notiz (2).txt"))
        r3 = await self._hochladen("a/b\\..\\geheim<script>.md", b"x")
        self.assertEqual(r3["name"], "geheim_script_.md")

    async def test_art_groesse_und_leere_datei(self):
        for name, inhalt, status in (("boese.exe", b"MZ", 400), ("seite.html", b"<html>", 400),
                                     (".versteckt", b"x", 400), ("leer.txt", b"", 400)):
            with self.subTest(name=name), self.assertRaises(HTTPException) as e:
                await self._hochladen(name, inhalt)
            self.assertEqual(e.exception.status_code, status)
        with self.assertRaises(HTTPException) as e:
            await self._hochladen("gross.txt", b"x" * (projekt_dateien.MAX_BYTES + 1))
        self.assertEqual(e.exception.status_code, 413)

    async def test_herunterladen_nur_als_anhang_und_nur_vorhandenes(self):
        await self._hochladen("bericht.txt", b"Inhalt")
        async with self.Session() as db:
            antwort = await api.datei_herunterladen("p1", "bericht.txt", user=MITGLIED, db=db)
            self.assertEqual(antwort.media_type, "application/octet-stream")
            self.assertIn("attachment", antwort.headers["content-disposition"])
            for falsch in ("../bericht.txt.md", "gibtsnicht.txt", "..%2Fx"):
                with self.subTest(falsch=falsch), self.assertRaises(HTTPException) as e:
                    await api.datei_herunterladen("p1", falsch, user=MITGLIED, db=db)
                self.assertEqual(e.exception.status_code, 404)

    async def test_loeschen_entfernt_original_und_seite(self):
        await self._hochladen("weg.txt", b"bald weg")
        async with self.Session() as db:
            await api.datei_loeschen("p1", "weg.txt", user=MITGLIED, db=db)
        brain = await self._brain()
        self.assertFalse(os.path.exists(brain.host_path + ".dateien/weg.txt"))
        with self.assertRaises(FileNotFoundError):
            vault.read_file(brain.host_path, "dateien/weg.txt.md")

    async def test_betrachter_und_fremdes_projekt(self):
        with self.assertRaises(HTTPException) as e:
            await self._hochladen("x.txt", b"x", user=BETRACHTER)
        self.assertEqual(e.exception.status_code, 403)
        with self.assertRaises(HTTPException) as e:
            await self._hochladen("x.txt", b"x", projekt="p2")
        self.assertEqual(e.exception.status_code, 403)
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as e:
                await api.dateien_liste("p2", user=MITGLIED, db=db)
        self.assertEqual(e.exception.status_code, 403)


class Sicherheit(Dateien):
    """Befunde des Sicherheits-Reviews vom 28.10.2026 (vor dem ersten Release)."""

    async def test_seite_einer_datei_ueberschreibt_keine_vorhandene_seite(self):
        await self._hochladen("erste.txt", b"legt das Projektwissen an")
        brain = await self._brain()
        vault.write_file(brain.host_path, "dateien/notiz.txt.md", "von Hand")
        r = await self._hochladen("notiz.txt", b"hochgeladen")
        self.assertEqual(r["name"], "notiz (2).txt")
        self.assertEqual(vault.read_file(brain.host_path, "dateien/notiz.txt.md"), "von Hand")

    async def test_dateien_ordner_ist_fuer_uploads_reserviert(self):
        await self._hochladen("vertrag.txt", b"Vertragstext")
        async with self.Session() as db:
            for pfad in ("dateien/vertrag.txt.md", "./dateien/neu.md", "x/../dateien/neu.md", "dateien"):
                with self.subTest(pfad=pfad), self.assertRaises(HTTPException) as e:
                    await api.wissen_schreiben("p1", api.WissenSeite(pfad=pfad, inhalt="ersetzt"), user=MITGLIED, db=db)
                self.assertEqual(e.exception.status_code, 400)
            with self.assertRaises(HTTPException) as e:
                await api.wissen_ergaenzen("p1", api.WissenAbschnitt(pfad="dateien/vertrag.txt.md", abschnitt="x"),
                                           user=MITGLIED, db=db)
            self.assertEqual(e.exception.status_code, 400)
            with self.assertRaises(HTTPException) as e:
                await api.wissen_verlauf("p1", "dateien/vertrag.txt.md", user=MITGLIED, db=db)
            self.assertEqual(e.exception.status_code, 404)
            # Andere Seiten bleiben frei beschreibbar.
            await api.wissen_schreiben("p1", api.WissenSeite(pfad="dateien-uebersicht.md", inhalt="ok"),
                                       user=MITGLIED, db=db)

    async def test_geloeschte_datei_lebt_nicht_im_verlauf_weiter(self):
        import subprocess

        await self._hochladen("geheim.txt", b"Kennwort-Liste")
        async with self.Session() as db:
            await api.datei_loeschen("p1", "geheim.txt", user=MITGLIED, db=db)
        brain = await self._brain()
        if os.path.isdir(os.path.join(brain.host_path, ".git")):
            log = subprocess.run(["git", "-C", brain.host_path, "log", "--all", "-p"], capture_output=True, text=True)
            self.assertNotIn("Kennwort-Liste", log.stdout)

    async def test_herunterladen_mit_nosniff(self):
        await self._hochladen("seite.txt", b"<script>alert(1)</script>")
        async with self.Session() as db:
            antwort = await api.datei_herunterladen("p1", "seite.txt", user=MITGLIED, db=db)
        self.assertEqual(antwort.headers["x-content-type-options"], "nosniff")

    async def test_fremdes_projekt_nicht_herunterladen_oder_loeschen(self):
        await self._hochladen("intern.txt", b"intern")
        async with self.Session() as db:
            for aufruf in (api.datei_herunterladen, api.datei_loeschen):
                with self.subTest(aufruf=aufruf.__name__), self.assertRaises(HTTPException) as e:
                    await aufruf("p2", "intern.txt", user=MITGLIED, db=db)
                self.assertEqual(e.exception.status_code, 403)

    async def test_meldung_zaehlt_nicht_als_ausgelesener_text(self):
        r = await self._hochladen("kaputt.docx", b"kein zip")
        self.assertFalse(r["text_ausgelesen"])


class Auslesen(unittest.TestCase):
    def test_zip_bombe_wird_nicht_entpackt(self):
        import zipfile

        from app.core import dokument_text

        puffer = io.BytesIO()
        with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", b"0" * (dokument_text.MAX_ENTPACKT + 1))
        text = dokument_text.text_auslesen(puffer.getvalue(), "bombe.docx", "")
        self.assertTrue(dokument_text.ist_meldung(text), text)
        self.assertIn("zu groß", text)

    def test_viele_excel_zeilen_bleiben_schnell(self):
        import time

        from app.core import dokument_text

        roh = _xlsx([[f"z{i}"] for i in range(20_000)])
        start = time.monotonic()
        text = dokument_text.text_auslesen(roh, "lang.xlsx", "", max_chars=10**9)
        self.assertLess(time.monotonic() - start, 15)
        self.assertIn("z19999", text)

    def test_ist_meldung(self):
        from app.core.dokument_text import ist_meldung

        self.assertTrue(ist_meldung("Das PDF ist passwortgeschützt — Inhalt nicht lesbar."))
        self.assertFalse(ist_meldung("Das PDF ist hier der Inhalt"))
        self.assertFalse(ist_meldung(None))


class Koerpergrenze(unittest.IsolatedAsyncioTestCase):
    async def _senden(self, pfad, kopf):
        from app.core.koerper_grenze import KoerperGrenze, regel

        regel("POST", r"^/test/upload$", 100)
        weiter, antworten = [], []

        async def app(scope, receive, send):
            weiter.append(scope["path"])

        async def send(nachricht):
            antworten.append(nachricht)

        await KoerperGrenze(app)({"type": "http", "method": "POST", "path": pfad, "headers": kopf}, None, send)
        return weiter, (antworten[0]["status"] if antworten else None)

    async def test_zu_gross_ohne_laenge_und_passend(self):
        self.assertEqual(await self._senden("/test/upload", [(b"content-length", b"101")]), ([], 413))
        self.assertEqual(await self._senden("/test/upload", []), ([], 411))
        self.assertEqual(await self._senden("/test/upload", [(b"content-length", b"100")]), (["/test/upload"], None))
        # Andere Pfade sind nicht betroffen.
        self.assertEqual(await self._senden("/test/anders", []), (["/test/anders"], None))

    def test_regel_fuer_projektdateien_ist_registriert(self):
        import app.main  # noqa: F401 — registriert die Regeln
        from app.core.koerper_grenze import REGELN

        self.assertTrue(any(m == "POST" and muster.match("/api/v1/teams/p1/wissen/dateien")
                            for m, muster, _ in REGELN))
