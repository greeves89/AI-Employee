"""Versandfertige Dokumente: ``dokument pdf`` statt Browser-Druck (#893).

Befund aus dem Markttest: Vom Agenten erzeugte PDFs trugen die Kopf- und
Fußzeile des Browsers — oben ein US-Datum („10/4/26, 2:31 PM“), unten den
Dateipfad (``file:///workspace/…``). Das Abbild hatte kein PDF-Werkzeug, also
improvisierten die Agenten mit ``chromium --print-to-pdf``.

Jetzt gibt es EIN Werkzeug für alle Laufzeiten (``dokument``), und
``present_file`` weist ein PDF mit Browser-Rand ab — mit dem Hinweis, wie es
richtig geht. Die Prüfung steckt in ``app.dokument``; der MCP-Server (Claude
Code) ruft sie über die Kommandozeile, der Executor (Codex/Custom-LLM) direkt.
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app import dokument

AGENT = Path(__file__).resolve().parents[1]
CLI = AGENT / "scripts" / "dokument"


def _mini_pdf(zeilen: list[str], breite: int = 595, hoehe: int = 842) -> bytes:
    """Ein einseitiges PDF mit Textzeilen — ohne Browser, ohne Bibliothek."""
    def esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    inhalt = "BT /F1 10 Tf 40 {} Td 14 TL ".format(hoehe - 40)
    inhalt += " ".join(f"({esc(z)}) Tj T*" for z in zeilen) + " ET"
    objekte = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {breite} {hoehe}] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(inhalt.encode('latin-1'))} >>\nstream\n{inhalt}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    aus = b"%PDF-1.4\n"
    versatz = []
    for nr, obj in enumerate(objekte, start=1):
        versatz.append(len(aus))
        aus += f"{nr} 0 obj\n{obj}\nendobj\n".encode("latin-1")
    xref = len(aus)
    aus += f"xref\n0 {len(objekte) + 1}\n0000000000 65535 f \n".encode()
    for v in versatz:
        aus += f"{v:010d} 00000 n \n".encode()
    aus += f"trailer\n<< /Size {len(objekte) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return aus


class _MitOrdner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ordner = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _pdf(self, name: str, zeilen: list[str], **kw) -> Path:
        pfad = self.ordner / name
        pfad.write_bytes(_mini_pdf(zeilen, **kw))
        return pfad


BROWSER_DRUCK = [
    "10/4/26, 2:31 PM                         Angebot",
    "Angebot Nr. 17",
    "file:///workspace/transfer/angebot.html                  1/1",
]


class PruefenTests(_MitOrdner):
    def test_browser_kopf_und_fusszeile_werden_erkannt(self):
        ergebnis = dokument.pruefe_pdf(self._pdf("roh.pdf", BROWSER_DRUCK))
        arten = {f["art"] for f in ergebnis["fehler"]}
        self.assertIn("browser_rand", arten)
        self.assertFalse(ergebnis["ok"])
        self.assertIn("dokument pdf", ergebnis["anzeige_meldung"])

    def test_sauberes_a4_dokument_besteht(self):
        ergebnis = dokument.pruefe_pdf(self._pdf("gut.pdf", ["Angebot Nr. 17", "Seite 1 von 1"]))
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertEqual(ergebnis["fehler"], [])
        self.assertEqual(ergebnis["format"], "A4")
        self.assertIsNone(ergebnis["anzeige_meldung"])

    def test_platzhalter_sind_ein_fehler_aber_kein_grund_zur_ablehnung_beim_anzeigen(self):
        ergebnis = dokument.pruefe_pdf(self._pdf("muster.pdf", ["Kunde: Muster GmbH"]))
        self.assertFalse(ergebnis["ok"])
        self.assertEqual({f["art"] for f in ergebnis["fehler"]}, {"platzhalter"})
        self.assertIsNone(ergebnis["anzeige_meldung"])

    def test_anderes_papierformat_ist_ein_hinweis(self):
        ergebnis = dokument.pruefe_pdf(self._pdf("letter.pdf", ["Text"], breite=612, hoehe=792))
        self.assertTrue(ergebnis["ok"])
        self.assertTrue(any("A4" in h for h in ergebnis["hinweise"]))

    def test_kommandozeile_meldet_fehler_ueber_den_exitcode(self):
        roh = self._pdf("roh.pdf", BROWSER_DRUCK)
        lauf = subprocess.run([sys.executable, str(CLI), "pruefen", "--json", str(roh)],
                              capture_output=True, text=True, cwd=AGENT)
        self.assertEqual(lauf.returncode, 1, lauf.stderr)
        self.assertIn("browser_rand", {f["art"] for f in json.loads(lauf.stdout)["fehler"]})


class PresentFileTests(_MitOrdner):
    def _executor(self):
        from app.tools.executor import ToolExecutor

        ex = ToolExecutor.__new__(ToolExecutor)
        ex._resolve_path = lambda p: p  # noqa: SLF001 — Pfadauflösung ist hier nicht Thema
        return ex

    def _zeige(self, pfad: Path) -> str:
        return asyncio.run(self._executor()._tool_present_file({"path": str(pfad)}))

    def test_pdf_mit_browser_rand_wird_abgewiesen(self):
        antwort = self._zeige(self._pdf("roh.pdf", BROWSER_DRUCK))
        self.assertTrue(antwort.startswith("Error"), antwort)
        self.assertIn("dokument pdf", antwort)

    def test_sauberes_pdf_wird_angezeigt(self):
        antwort = self._zeige(self._pdf("gut.pdf", ["Angebot", "Seite 1 von 1"]))
        self.assertTrue(antwort.startswith("__AI_EMPLOYEE_PRESENT_FILE__"), antwort)

    def test_andere_dateien_bleiben_unberuehrt(self):
        datei = self.ordner / "notiz.txt"
        datei.write_text("file:///workspace/x 10/4/26, 2:31 PM")
        antwort = self._zeige(datei)
        self.assertTrue(antwort.startswith("__AI_EMPLOYEE_PRESENT_FILE__"), antwort)


MARKDOWN = """# Angebot 2026-017

Sehr geehrte Damen und Herren,

vielen Dank für Ihre Anfrage. Wir bieten an:

| Pos. | Leistung | Menge | Preis |
|---|---|---|---|
| 1 | Wand streichen | 42,50 m² | 510,00 € |

- Gültig 30 Tage
- Zahlbar netto 14 Tage
"""


def _browser_verfuegbar() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            pfad = dokument._chromium_pfad()
            browser = pw.chromium.launch(**({"executable_path": pfad} if pfad else {}))
            browser.close()
        return True
    except Exception:  # noqa: BLE001
        return False


class PdfErzeugenTests(_MitOrdner):
    @classmethod
    def setUpClass(cls):
        if not _browser_verfuegbar():
            raise unittest.SkipTest("Kein Chromium für Playwright verfügbar")

    def test_markdown_wird_zu_a4_ohne_browser_rand_mit_seitenzahl(self):
        quelle = self.ordner / "angebot.md"
        quelle.write_text(MARKDOWN, encoding="utf-8")
        ziel = self.ordner / "angebot.pdf"
        lauf = subprocess.run([sys.executable, str(CLI), "pdf", str(quelle), "-o", str(ziel)],
                              capture_output=True, text=True, cwd=AGENT)
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        ergebnis = dokument.pruefe_pdf(ziel)
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertEqual(ergebnis["format"], "A4")
        text = dokument._pdf_text(ziel)[1]
        self.assertIn("Seite 1 von 1", text)
        self.assertIn("Wand streichen", text)
        self.assertNotIn("file://", text)


class _Zaehler:
    """Kleiner HTTP-Server, der nur zählt, ob jemand anklopft."""

    def __enter__(self):
        import http.server
        import threading

        zaehler = self
        self.anfragen = 0

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                zaehler.anfragen += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"GEHEIM-AUS-DEM-NETZ")

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()


# Kleinstes gültiges PNG (1×1), für erlaubte und verbotene Bilder.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


class RenderHaertungTests(_MitOrdner):
    """Eingaben können aus fremden Quellen stammen (Webseiten, Prompt-Injektion).

    Das Rendern darf deshalb weder das interne Netz anfragen noch Dateien
    außerhalb des Ordners des Quelldokuments einbinden (#893, Sicherheitsprüfung).
    """

    @classmethod
    def setUpClass(cls):
        if not _browser_verfuegbar():
            raise unittest.SkipTest("Kein Chromium für Playwright verfügbar")

    def setUp(self):
        super().setUp()
        self.draussen = Path(tempfile.mkdtemp())
        (self.draussen / "geheim.txt").write_text("GEHEIM-AUS-DER-DATEI", encoding="utf-8")
        (self.draussen / "geheim.png").write_bytes(PNG)
        self.doc = self.ordner / "doc"
        self.doc.mkdir()
        (self.doc / "erlaubt.png").write_bytes(PNG)
        os.symlink(self.draussen / "geheim.png", self.doc / "link.png")
        os.symlink(self.draussen / "geheim.txt", self.doc / "link.txt")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.draussen, ignore_errors=True)
        super().tearDown()

    def _rendern(self, name: str, inhalt: str):
        quelle = self.doc / name
        quelle.write_text(inhalt, encoding="utf-8")
        ziel = self.ordner / "aus.pdf"
        bericht = dokument.erzeuge_pdf(quelle, ziel)
        return bericht, dokument._pdf_text(ziel)[1]

    def _angriff(self, netz: str) -> str:
        geheim = (self.draussen / "geheim.txt").as_uri()
        return (
            f'<img src="{geheim}">\n'
            f'<img src="file:///etc/passwd">\n'
            f'<iframe src="{netz}/orchestrator"></iframe>\n'
            f'<iframe src="{geheim}"></iframe>\n'
            f'<object data="{geheim}"></object><embed src="{geheim}">\n'
            f'<link rel="stylesheet" href="{netz}/meta-data">\n'
            f'<meta http-equiv="refresh" content="0; url={netz}/weiter">\n'
            f'<script>fetch("{netz}/js")</script>\n'
            f'<img src="{netz}/bild.png">\n'
            f'<div style="background:url({netz}/css.png)">Hintergrund</div>\n'
            '<img src="link.png"><img src="../../etc/passwd">\n'
            '<iframe src="link.txt"></iframe>\n'
            '<img src="erlaubt.png">\n'
        )

    def test_markdown_mit_angriffen_fragt_nichts_an_und_bindet_nichts_ein(self):
        with _Zaehler() as netz:
            bericht, text = self._rendern("a.md", "# Titel\n\n" + self._angriff(netz.url))
            self.assertEqual(netz.anfragen, 0, "das Rendern hat das Netz angefragt")
        self.assertNotIn("GEHEIM", text)
        self.assertIn("Titel", text)
        self.assertIn("erlaubt.png", " ".join(bericht["erlaubt"]))
        self.assertFalse(any("link.png" in u for u in bericht["erlaubt"]))

    def test_html_mit_angriffen_fragt_nichts_an_und_bindet_nichts_ein(self):
        with _Zaehler() as netz:
            html_text = ("<html><head><base href=\"" + netz.url + "/\"></head><body><h1>Titel</h1>"
                         + self._angriff(netz.url) + "</body></html>")
            bericht, text = self._rendern("a.html", html_text)
            self.assertEqual(netz.anfragen, 0, "das Rendern hat das Netz angefragt")
        self.assertNotIn("GEHEIM", text)
        self.assertIn("Titel", text)


class RessourcenWeicheTests(_MitOrdner):
    """Was beim Rendern aus dem Ordner kommen darf — ohne Browser prüfbar.

    Ein Regex-Bereiniger lässt sich verschachtelt umgehen (``<ifr<iframe>ame``);
    tragend ist deshalb die Weiche: nur Bilder, Schriften, Stylesheets — nie ein
    Frame oder eine Textdatei aus demselben Ordner (Sicherheitsprüfung #893).
    """

    def setUp(self):
        super().setUp()
        (self.ordner / "bild.png").write_bytes(b"x")
        (self.ordner / "stil.css").write_text("p{}", encoding="utf-8")
        (self.ordner / "notizen.txt").write_text("GEHEIM", encoding="utf-8")
        (self.ordner / ".env").write_text("GEHEIM", encoding="utf-8")

    def _weiche(self, pfad: str, typ: str):
        return dokument._ressource_aus_ordner(self.ordner, dokument.HERKUNFT + pfad, typ)

    def test_bild_und_stylesheet_aus_dem_ordner(self):
        self.assertIsNotNone(self._weiche("bild.png", "image"))
        self.assertIsNotNone(self._weiche("stil.css", "stylesheet"))

    def test_textdateien_und_frames_bleiben_draussen(self):
        for pfad, typ in [("notizen.txt", "document"), ("notizen.txt", "image"), (".env", "stylesheet"),
                          ("bild.png", "document"), ("bild.png", "other"), ("stil.css", "image"),
                          ("bild.png?x=1", "image"), ("../bild.png", "image")]:
            with self.subTest(pfad=pfad, typ=typ):
                self.assertIsNone(self._weiche(pfad, typ))

    def test_fremde_herkunft_wird_nie_bedient(self):
        self.assertIsNone(dokument._ressource_aus_ordner(self.ordner, "http://orchestrator:8000/bild.png", "image"))

    def test_verschachtelte_elemente_fallen_vollstaendig(self):
        for angriff in ['<ifr<iframe>ame src="notizen.txt"></ifr</iframe>ame>',
                        '<scr<script></script>ipt>x</scr<script></script>ipt>',
                        '<obj<object></object>ect data="notizen.txt">',
                        '<p o<b>nload=x</b>>t</p>']:
            with self.subTest(angriff=angriff):
                aus = dokument._entschaerfen(angriff).lower()
                for rest in ("<iframe", "<script", "<object", "<embed"):
                    self.assertNotIn(rest, aus)


class DocxTests(_MitOrdner):
    def test_markdown_wird_zu_word_mit_ueberschrift_tabelle_und_liste(self):
        import docx

        quelle = self.ordner / "angebot.md"
        quelle.write_text(MARKDOWN, encoding="utf-8")
        ziel = self.ordner / "angebot.docx"
        lauf = subprocess.run([sys.executable, str(CLI), "docx", str(quelle), "-o", str(ziel)],
                              capture_output=True, text=True, cwd=AGENT)
        self.assertEqual(lauf.returncode, 0, lauf.stdout + lauf.stderr)
        doc = docx.Document(str(ziel))
        absaetze = [p for p in doc.paragraphs if p.text.strip()]
        self.assertEqual(absaetze[0].text, "Angebot 2026-017")
        self.assertTrue(absaetze[0].style.name.startswith("Heading"))
        self.assertEqual(len(doc.tables), 1)
        self.assertEqual(doc.tables[0].cell(1, 1).text, "Wand streichen")
        listen = [p for p in doc.paragraphs if p.style.name.startswith("List")]
        self.assertEqual([p.text for p in listen], ["Gültig 30 Tage", "Zahlbar netto 14 Tage"])
        abschnitt = doc.sections[0]
        self.assertAlmostEqual(abschnitt.page_width.mm, 210, delta=1)
        self.assertAlmostEqual(abschnitt.page_height.mm, 297, delta=1)


class AnleitungTests(unittest.TestCase):
    """Die Regel „PDF nur mit `dokument pdf`“ erreicht jede Laufzeit und jeden Kanal."""

    REGEL = "dokument pdf"

    def test_fertige_ergebnisse_in_der_gemeinsamen_anleitung(self):
        from app.runner_hooks import MULTIMODAL_CAPABILITY_NOTE

        self.assertIn(self.REGEL, MULTIMODAL_CAPABILITY_NOTE)
        self.assertIn("--print-to-pdf", MULTIMODAL_CAPABILITY_NOTE)

    def test_chat_und_telegram_bekommen_die_regel(self):
        from app import chat_consumer

        kanal = chat_consumer._build_channel_prompt("Schick mir das Angebot als PDF", "web", True)
        telegram = chat_consumer._build_telegram_prompt("Schick das PDF", {"chat_id": 1}, True)
        self.assertIn(self.REGEL, kanal)
        self.assertIn(self.REGEL, telegram)

    def test_claude_code_anleitung(self):
        self.assertIn(self.REGEL, (AGENT / "claude-global.md").read_text(encoding="utf-8"))


# ─── Seitenumbruch: keine Folgeseite mit nur dem Fußblock (Abnahme v1.362.1) ─────
#
# Befund: Seite 2 eines kurzen Angebots enthielt nur den Fußblock (Firma, Register,
# Bank). Zwei Wege führen dorthin, beide nachgestellt:
#   * Markdown, das knapp über eine Seite reicht: der Fußblock nach der letzten
#     Trennlinie rutschte allein (oder zerrissen) auf die nächste Seite;
#   * eigenes HTML des Agenten mit Seitenbehälter `min-height: 297mm` und absolut
#     gesetzter Fußzeile: zusammen mit unseren Rändern ist der Behälter höher als
#     die Seite, die Fußzeile landet allein auf Seite 2.

FUSS = (
    "Malerbetrieb Beispiel GmbH · Sitz Beispielstadt · Amtsgericht Beispielstadt, HRB 12345 · "
    "Geschäftsführung: Erika Beispiel\n"
    "Steuernummer: 12/345/67890 · Beispielbank, IBAN DE00 0000 0000 0000 0000 00, BIC XXXXDEXXXXX\n"
)


def _angebot_md(extra_positionen: int = 0) -> str:
    zeilen = "".join(
        f"| 01.{i + 4:02d} | Wandfläche streichen, zwei Anstriche, Dispersionsfarbe weiß "
        f"| 10,00 | m² | 9,80 € | 98,00 € |\n"
        for i in range(extra_positionen)
    )
    return (
        "**Malerbetrieb Beispiel GmbH** · Hauptstraße 5 · 12345 Beispielstadt\n\n"
        "Familie Kunde  \nGartenweg 3  \n12345 Beispielstadt\n\n"
        "# Angebot 2026-017 – Renovierung Wohnzimmer\n"
        "Datum: 04.10.2026 · Ihre Anfrage vom 01.10.2026 · Ansprechpartner: Herr Beispiel\n\n"
        "Sehr geehrte Familie Kunde, vielen Dank für Ihre Anfrage. Wir bieten Ihnen an:\n\n"
        "| Pos. | Leistung | Menge | Einheit | Einzelpreis netto | Gesamt netto |\n"
        "|---|---|---|---|---|---|\n"
        "| 01.01 | Untergrund vorbereiten, Löcher spachteln, Flächen schleifen | 40,00 | m² | 3,50 € | 140,00 € |\n"
        "| 01.02 | Wandflächen weiß streichen, zwei Anstriche | 40,00 | m² | 9,80 € | 392,00 € |\n"
        + zeilen +
        "| 01.03 | Abdecken und Abkleben von Boden, Fenstern und Türen | 1,00 | psch | 85,00 € | 85,00 € |\n\n"
        "| | Betrag |\n|---|---|\n| Summe netto | 617,00 € |\n| Umsatzsteuer 19 % | 117,23 € |\n"
        "| **Summe brutto** | 734,23 € |\n\n"
        "Darin enthaltene Arbeitskosten: 480,00 € (für § 35a EStG).\n\n"
        "Bindefrist 30 Tage. Zahlbar innerhalb von 14 Tagen nach Rechnungsstellung ohne Abzug. "
        "Ausführung nach Absprache, voraussichtlich KW 44.\n\n"
        "Mit freundlichen Grüßen\n\nIhr Malerteam Beispiel\n\n"
        "---\n" + FUSS
    )


ANGEBOT_HTML_SEITENHOCH = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body { margin: 0; font-family: Arial, sans-serif; font-size: 10pt; }
.page { min-height: 297mm; padding: 20mm 20mm 30mm 20mm; box-sizing: border-box; position: relative; }
.footer { position: absolute; bottom: 12mm; left: 20mm; right: 20mm; font-size: 7.5pt; }
</style></head><body><div class="page">
<p>Malerbetrieb Beispiel GmbH · Hauptstraße 5 · 12345 Beispielstadt</p>
<h1>Angebot 2026-017</h1>
<table><tr><th>Pos.</th><th>Leistung</th><th>GP</th></tr>
<tr><td>1</td><td>Wände streichen, zwei Anstriche</td><td>392,00 €</td></tr></table>
<p>Summe brutto 466,48 €</p><p>Mit freundlichen Grüßen</p>
<div class="footer">Sitz Beispielstadt · HRB 12345 · IBAN DE00 0000 0000 0000 0000 00</div>
</div></body></html>"""


def _seiten(pfad: Path) -> list[str]:
    from pypdf import PdfReader

    return [(p.extract_text() or "") for p in PdfReader(str(pfad)).pages]


class LetzteSeiteFastLeerTests(unittest.TestCase):
    """Die Erkennung selbst — ohne Browser."""

    def test_nur_fussblock_auf_der_letzten_seite(self):
        voll = "Position 01.01 Wandfläche streichen 98,00 €\n" * 40
        self.assertTrue(dokument.letzte_seite_fast_leer([voll, "Sitz Beispielstadt · HRB 12345\nSeite 2 von 2"]))

    def test_gut_gefuellte_letzte_seite_ist_in_ordnung(self):
        voll = "Position 01.01 Wandfläche streichen 98,00 €\n" * 40
        self.assertFalse(dokument.letzte_seite_fast_leer([voll, voll[: len(voll) // 2]]))

    def test_eine_seite_ist_nie_fast_leer(self):
        self.assertFalse(dokument.letzte_seite_fast_leer(["kurz"]))

    def test_platzhalter_in_eckigen_klammern(self):
        with tempfile.TemporaryDirectory() as tmp:
            pfad = Path(tmp) / "a.pdf"
            pfad.write_bytes(_mini_pdf(["Angebot von [Ihr Firmenname]", "Seite 1 von 1"]))
            ergebnis = dokument.pruefe_pdf(pfad)
        self.assertIn("platzhalter", {f["art"] for f in ergebnis["fehler"]})


class SeitenumbruchTests(_MitOrdner):
    @classmethod
    def setUpClass(cls):
        if not _browser_verfuegbar():
            raise unittest.SkipTest("Kein Chromium für Playwright verfügbar")

    def _pdf_aus(self, name: str, inhalt: str) -> list[str]:
        quelle = self.ordner / name
        quelle.write_text(inhalt, encoding="utf-8")
        ziel = quelle.with_suffix(".pdf")
        dokument.erzeuge_pdf(quelle, ziel, fusszeile="Malerbetrieb Beispiel GmbH")
        return _seiten(ziel)

    def test_kurzes_angebot_passt_auf_eine_seite(self):
        seiten = self._pdf_aus("kurz.md", _angebot_md())
        self.assertEqual(1, len(seiten), seiten)
        self.assertIn("Seite 1 von 1", seiten[0])
        self.assertIn("HRB 12345", seiten[0])

    def test_fussblock_steht_nie_allein_und_nie_zerrissen_auf_der_folgeseite(self):
        # 5 und 6 Zusatzpositionen: ohne Schutz zerriss der Fußblock bzw. stand
        # allein auf Seite 2 (nachgemessen vor der Änderung).
        for extra in (4, 5, 6, 7):
            with self.subTest(extra=extra):
                seiten = self._pdf_aus(f"lang{extra}.md", _angebot_md(extra))
                mit_fuss = [s for s in seiten if "Sitz Beispielstadt" in s]
                self.assertEqual(1, len(mit_fuss), seiten)
                self.assertIn("BIC XXXXDEXXXXX", mit_fuss[0], "Fußblock zerrissen")
                inhalt = ("Ihr Malerteam Beispiel", "Mit freundlichen Grüßen", "Bindefrist", "Summe")
                self.assertTrue(any(w in mit_fuss[0] for w in inhalt),
                                f"Seite trägt nur den Fußblock: {mit_fuss[0]!r}")

    def test_eigenes_html_mit_seitenhoehe_bleibt_auf_einer_seite(self):
        seiten = self._pdf_aus("seitenhoch.html", ANGEBOT_HTML_SEITENHOCH)
        self.assertEqual(1, len(seiten), seiten)
        self.assertIn("HRB 12345", seiten[0])
        self.assertIn("Mit freundlichen Grüßen", seiten[0])
        self.assertIn("Seite 1 von 1", seiten[0])


if __name__ == "__main__":
    os.environ.setdefault("PYTHONPATH", str(AGENT))
    unittest.main()
