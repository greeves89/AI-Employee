"""Versandfertige Dokumente: PDF und Word aus Markdown/HTML, und deren Prüfung (#893).

Kommandozeile: ``dokument`` (``agent/scripts/dokument``, im Abbild unter
/usr/local/bin). Alle drei Laufzeiten — Claude Code, Codex, Custom-LLM — rufen
dasselbe Werkzeug über ihre Shell auf; es gibt keine zweite Umsetzung.

    dokument pdf  <eingabe.md|.html> [-o ziel.pdf] [--titel T] [--fusszeile T]
    dokument docx <eingabe.md> [-o ziel.docx]
    dokument pruefen [--json] <datei.pdf>

Warum es das gibt: Ohne Werkzeug druckten die Agenten mit ``chromium
--print-to-pdf``. Das setzt die Standard-Kopfzeile des Browsers (US-Datum,
Titel) und die Fußzeile (``file:///workspace/…``, Seitenzahl „1/3“) auf jede
Seite — so ging kein Angebot an einen Kunden.

``pruefe_pdf`` ist zugleich die Prüfung, mit der ``present_file`` ein solches
PDF abweist: der Executor ruft sie direkt, der MCP-Server über
``dokument pruefen --json``.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

# ─── Prüfung ──────────────────────────────────────────────────────────────────

#: Spuren der Standard-Kopf-/Fußzeile eines Browsers. Bewusst eng gefasst:
#: ein Dateipfad als URL und das amerikanische Datum MIT Uhrzeit, wie Chromium
#: es druckt („10/4/26, 2:31 PM“). Ein Bruch wie „3/4“ allein schlägt nicht an.
BROWSER_RAND = (
    (re.compile(r"file://", re.I), "Dateipfad (file://) aus der Fußzeile des Browsers"),
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}\s*(?:AM|PM)\b", re.I),
     "US-Datum mit Uhrzeit aus der Kopfzeile des Browsers"),
)

#: Platzhalter, die in keinem versandfertigen Dokument stehen dürfen.
PLATZHALTER = (
    "Muster GmbH", "Musterfirma", "Max Mustermann", "Erika Mustermann",
    "Musterstraße 1", "Lorem ipsum", "[Firmenname]", "<Firmenname>",
)

#: A4 in Punkt (1/72 Zoll); Toleranz für Rundung der Erzeuger.
A4_PT = (595.3, 841.9)
_TOLERANZ_PT = 3.0

ANZEIGE_HINWEIS = (
    "Erzeuge das PDF neu mit `dokument pdf <eingabe.md|.html> -o <ziel.pdf>` — "
    "das setzt A4, eine eigene Fußzeile „Seite X von Y“ und keinen Browser-Rand. "
    "Danach erneut present_file aufrufen."
)


def _pdf_text(pfad: str | os.PathLike) -> tuple[list[tuple[float, float]], str]:
    """(Seitengrößen in pt, gesamter Text). pymupdf im Abbild, sonst pypdf."""
    try:
        import fitz  # pymupdf

        with fitz.open(str(pfad)) as doc:
            groessen = [(p.rect.width, p.rect.height) for p in doc]
            text = "\n".join(p.get_text() for p in doc)
        return groessen, text
    except ImportError:
        pass
    from pypdf import PdfReader

    reader = PdfReader(str(pfad))
    groessen = [(float(p.mediabox.width), float(p.mediabox.height)) for p in reader.pages]
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    return groessen, text


def _ist_a4(breite: float, hoehe: float) -> bool:
    b, h = sorted((breite, hoehe))
    return abs(b - A4_PT[0]) <= _TOLERANZ_PT and abs(h - A4_PT[1]) <= _TOLERANZ_PT


def pruefe_pdf(pfad: str | os.PathLike) -> dict:
    """Ist das PDF versandfertig?

    ``fehler`` (je ``art`` + ``text``): ``browser_rand`` oder ``platzhalter``.
    ``hinweise``: z. B. ein anderes Papierformat als A4.
    ``anzeige_meldung``: gesetzt, wenn ``present_file`` das PDF abweisen soll —
    nur bei Browser-Rand; ein Platzhalter kann in einem Entwurf berechtigt sein.
    """
    fehler: list[dict] = []
    hinweise: list[str] = []
    try:
        groessen, text = _pdf_text(pfad)
    except Exception as e:  # noqa: BLE001 — kaputtes PDF ist ein Befund, kein Absturz
        return {
            "ok": False, "seiten": 0, "format": None, "hinweise": [],
            "fehler": [{"art": "unlesbar", "text": f"PDF nicht lesbar: {e}"}],
            "anzeige_meldung": None,
        }

    for muster, beschreibung in BROWSER_RAND:
        treffer = muster.search(text)
        if treffer:
            fehler.append({"art": "browser_rand", "text": f"{beschreibung}: „{treffer.group(0)}“"})

    klein = text.lower()
    for platzhalter in PLATZHALTER:
        if platzhalter.lower() in klein:
            fehler.append({"art": "platzhalter", "text": f"Platzhalter im Text: „{platzhalter}“"})

    alle_a4 = bool(groessen) and all(_ist_a4(b, h) for b, h in groessen)
    if groessen and not alle_a4:
        b, h = groessen[0]
        hinweise.append(f"Papierformat ist nicht A4 ({b:.0f} × {h:.0f} pt).")

    rand = [f["text"] for f in fehler if f["art"] == "browser_rand"]
    meldung = None
    if rand:
        meldung = ("Dieses PDF trägt die Kopf-/Fußzeile eines Browsers ("
                   + "; ".join(rand) + ") und ist so nicht versandfertig. " + ANZEIGE_HINWEIS)
    return {
        "ok": not fehler,
        "seiten": len(groessen),
        "format": "A4" if alle_a4 else ("anderes" if groessen else None),
        "fehler": fehler,
        "hinweise": hinweise,
        "anzeige_meldung": meldung,
    }


# ─── PDF erzeugen ─────────────────────────────────────────────────────────────

DRUCK_CSS = """
@page { size: A4; margin: 20mm 18mm 22mm 18mm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: "Liberation Sans", "DejaVu Sans", Arial, Helvetica, sans-serif;
       font-size: 10.5pt; line-height: 1.45; color: #1a1a1a; margin: 0; }
h1 { font-size: 18pt; margin: 0 0 10pt; }
h2 { font-size: 14pt; margin: 16pt 0 6pt; }
h3 { font-size: 12pt; margin: 12pt 0 4pt; }
p { margin: 0 0 7pt; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0 10pt; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th, td { border: 0.6pt solid #9a9a9a; padding: 3pt 5pt; vertical-align: top; text-align: left; }
th { background: #eeeeee; }
code, pre { font-family: "DejaVu Sans Mono", "Liberation Mono", monospace; font-size: 9pt; }
pre { white-space: pre-wrap; }
img { max-width: 100%; }
.seitenumbruch { page-break-after: always; }
"""

_FUSSZEILE = (
    '<div style="width:100%;font-size:8pt;color:#555;padding:0 18mm;'
    'font-family:Liberation Sans,DejaVu Sans,Arial,sans-serif;display:flex;'
    'justify-content:space-between;">'
    '<span>{links}</span>'
    '<span>Seite <span class="pageNumber"></span> von <span class="totalPages"></span></span>'
    "</div>"
)


def _markdown_zu_html(text: str) -> str:
    import markdown

    return markdown.markdown(
        text, extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
        output_format="html5",
    )


def _erste_ueberschrift(html_text: str) -> str | None:
    treffer = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, re.S | re.I)
    if not treffer:
        return None
    return html.unescape(re.sub(r"<[^>]+>", "", treffer.group(1))).strip() or None


def baue_html(eingabe: Path, titel: str | None = None) -> str:
    """Vollständiges HTML-Dokument (lang=de, Druck-CSS) aus Markdown oder HTML."""
    roh = eingabe.read_text(encoding="utf-8")
    basis = eingabe.resolve().parent.as_uri() + "/"
    if eingabe.suffix.lower() in (".md", ".markdown", ".txt"):
        koerper = _markdown_zu_html(roh)
        titel = titel or _erste_ueberschrift(koerper) or eingabe.stem
        return (
            '<!doctype html><html lang="de"><head><meta charset="utf-8">'
            f'<base href="{html.escape(basis)}"><title>{html.escape(titel)}</title>'
            f"<style>{DRUCK_CSS}</style></head><body>{koerper}</body></html>"
        )
    # HTML: eigenes CSS des Agenten bleibt maßgeblich — unseres kommt ZUERST,
    # damit es nur füllt, was dort fehlt (A4, Schrift, Tabellenränder).
    kopf = f'<meta charset="utf-8"><base href="{html.escape(basis)}"><style>{DRUCK_CSS}</style>'
    if titel:
        kopf += f"<title>{html.escape(titel)}</title>"
    if re.search(r"<head[^>]*>", roh, re.I):
        dokument_html = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + kopf, roh, count=1, flags=re.I)
    elif re.search(r"<html[^>]*>", roh, re.I):
        dokument_html = re.sub(r"(<html[^>]*>)", lambda m: m.group(1) + "<head>" + kopf + "</head>",
                               roh, count=1, flags=re.I)
    else:
        dokument_html = f'<!doctype html><html lang="de"><head>{kopf}</head><body>{roh}</body></html>'
    if not re.search(r"<html[^>]*\blang=", dokument_html, re.I):
        dokument_html = re.sub(r"<html", '<html lang="de"', dokument_html, count=1, flags=re.I)
    return dokument_html


def _chromium_pfad() -> str | None:
    """Dieselbe Browserwahl wie das Browser-Werkzeug des Agenten."""
    try:
        from app.tools.browser import _chromium_pfad as browser_pfad

        return browser_pfad()
    except Exception:  # noqa: BLE001 — außerhalb des Abbilds: Playwright-Standard
        return None


def erzeuge_pdf(eingabe: Path, ziel: Path, titel: str | None = None, fusszeile: str = "") -> None:
    from playwright.sync_api import sync_playwright

    seite_html = baue_html(eingabe, titel)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".html", encoding="utf-8", delete=False) as tmp:
        tmp.write(seite_html)
        tmp_pfad = Path(tmp.name)
    try:
        with sync_playwright() as pw:
            pfad = _chromium_pfad()
            browser = pw.chromium.launch(
                args=["--no-sandbox", "--disable-dev-shm-usage"],
                **({"executable_path": pfad} if pfad else {}),
            )
            try:
                seite = browser.new_page(locale="de-DE")
                seite.goto(tmp_pfad.as_uri(), wait_until="networkidle")
                seite.pdf(
                    path=str(ziel),
                    format="A4",
                    print_background=True,
                    display_header_footer=True,
                    # Leere Kopfzeile statt der Standardzeile (Datum + Titel).
                    header_template="<span></span>",
                    footer_template=_FUSSZEILE.format(links=html.escape(fusszeile)),
                    margin={"top": "20mm", "bottom": "22mm", "left": "18mm", "right": "18mm"},
                )
            finally:
                browser.close()
    finally:
        tmp_pfad.unlink(missing_ok=True)


# ─── Word erzeugen ────────────────────────────────────────────────────────────

class _DocxBauer(HTMLParser):
    """Wandelt das HTML aus ``markdown`` in ein Word-Dokument um.

    Abgedeckt ist, was Markdown erzeugt: Überschriften, Absätze, Listen,
    Tabellen, fett/kursiv/Code, Zeilenumbruch, Trennlinie.
    """

    def __init__(self, doc):
        super().__init__(convert_charrefs=True)
        self.doc = doc
        self.absatz = None
        self.fett = self.kursiv = self.code = 0
        self.listen: list[str] = []
        self.tabelle_zeilen: list[list[str]] | None = None
        self.zelle: list[str] | None = None
        self.kopfzeile = False

    def _neuer_absatz(self, stil: str | None = None):
        self.absatz = self.doc.add_paragraph(style=stil) if stil else self.doc.add_paragraph()

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.absatz = self.doc.add_heading(level=min(int(tag[1]), 4))
        elif tag == "p" and self.zelle is None:
            if not self.listen:
                self._neuer_absatz()
        elif tag in ("ul", "ol"):
            self.listen.append(tag)
        elif tag == "li":
            self._neuer_absatz("List Number" if self.listen and self.listen[-1] == "ol" else "List Bullet")
        elif tag in ("strong", "b"):
            self.fett += 1
        elif tag in ("em", "i"):
            self.kursiv += 1
        elif tag == "code":
            self.code += 1
        elif tag == "br":
            if self.zelle is not None:
                self.zelle.append("\n")
            elif self.absatz is not None:
                self.absatz.add_run().add_break()
        elif tag == "table":
            self.tabelle_zeilen = []
        elif tag == "tr" and self.tabelle_zeilen is not None:
            self.tabelle_zeilen.append([])
        elif tag in ("td", "th"):
            self.zelle = []
        elif tag == "hr":
            self._neuer_absatz()
        elif tag == "pre":
            self._neuer_absatz()

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre"):
            if tag != "p" or self.zelle is None:
                self.absatz = None
        elif tag in ("ul", "ol") and self.listen:
            self.listen.pop()
        elif tag in ("strong", "b"):
            self.fett = max(0, self.fett - 1)
        elif tag in ("em", "i"):
            self.kursiv = max(0, self.kursiv - 1)
        elif tag == "code":
            self.code = max(0, self.code - 1)
        elif tag in ("td", "th") and self.zelle is not None and self.tabelle_zeilen:
            self.tabelle_zeilen[-1].append("".join(self.zelle).strip())
            self.zelle = None
        elif tag == "table" and self.tabelle_zeilen is not None:
            self._tabelle_schreiben(self.tabelle_zeilen)
            self.tabelle_zeilen = None

    def handle_data(self, data):
        if self.zelle is not None:
            self.zelle.append(data)
            return
        if not data.strip() and self.absatz is None:
            return
        if self.absatz is None:
            self._neuer_absatz()
        run = self.absatz.add_run(data)
        run.bold = bool(self.fett) or None
        run.italic = bool(self.kursiv) or None
        if self.code:
            run.font.name = "DejaVu Sans Mono"

    def _tabelle_schreiben(self, zeilen):
        zeilen = [z for z in zeilen if z]
        if not zeilen:
            return
        spalten = max(len(z) for z in zeilen)
        tabelle = self.doc.add_table(rows=len(zeilen), cols=spalten)
        try:
            tabelle.style = "Table Grid"
        except Exception:  # noqa: BLE001 — Vorlage ohne diesen Stil
            pass
        for r, zeile in enumerate(zeilen):
            for c, wert in enumerate(zeile):
                zelle = tabelle.cell(r, c)
                zelle.text = wert
                if r == 0:
                    for run in zelle.paragraphs[0].runs:
                        run.bold = True


def erzeuge_docx(eingabe: Path, ziel: Path) -> None:
    import docx
    from docx.oxml.ns import qn
    from docx.shared import Mm

    roh = eingabe.read_text(encoding="utf-8")
    inhalt = roh if eingabe.suffix.lower() in (".html", ".htm") else _markdown_zu_html(roh)
    doc = docx.Document()
    for abschnitt in doc.sections:
        abschnitt.page_width, abschnitt.page_height = Mm(210), Mm(297)
        abschnitt.left_margin = abschnitt.right_margin = Mm(20)
        abschnitt.top_margin = abschnitt.bottom_margin = Mm(20)
    # Sprache Deutsch für Silbentrennung und Rechtschreibprüfung in Word.
    stil = doc.styles["Normal"]
    rpr = stil.element.get_or_add_rPr()
    sprache = rpr.find(qn("w:lang"))
    if sprache is None:
        sprache = rpr.makeelement(qn("w:lang"), {})
        rpr.append(sprache)
    sprache.set(qn("w:val"), "de-DE")
    doc.core_properties.language = "de-DE"
    doc.core_properties.title = _erste_ueberschrift(inhalt) or eingabe.stem

    _DocxBauer(doc).feed(inhalt)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(ziel))


# ─── Kommandozeile ────────────────────────────────────────────────────────────

def _bericht(ergebnis: dict) -> str:
    zeilen = [f"{ergebnis['seiten']} Seite(n), Format: {ergebnis['format'] or 'unbekannt'}"]
    zeilen += [f"FEHLER ({f['art']}): {f['text']}" for f in ergebnis["fehler"]]
    zeilen += [f"Hinweis: {h}" for h in ergebnis["hinweise"]]
    zeilen.append("versandfertig" if ergebnis["ok"] else "NICHT versandfertig")
    return "\n".join(zeilen)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dokument",
        description="Versandfertige PDF- und Word-Dokumente aus Markdown/HTML erzeugen und prüfen.",
    )
    befehle = parser.add_subparsers(dest="befehl", required=True)

    p_pdf = befehle.add_parser("pdf", help="PDF (A4, Fußzeile „Seite X von Y“, ohne Browser-Rand)")
    p_pdf.add_argument("eingabe")
    p_pdf.add_argument("-o", "--ausgabe")
    p_pdf.add_argument("--titel")
    p_pdf.add_argument("--fusszeile", default="", help="Text links in der Fußzeile, z. B. Firmenname")

    p_docx = befehle.add_parser("docx", help="Word-Dokument (A4, Deutsch) aus Markdown")
    p_docx.add_argument("eingabe")
    p_docx.add_argument("-o", "--ausgabe")

    p_pruefen = befehle.add_parser("pruefen", help="PDF auf Browser-Rand, Platzhalter und A4 prüfen")
    p_pruefen.add_argument("datei")
    p_pruefen.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.befehl == "pruefen":
        ergebnis = pruefe_pdf(args.datei)
        print(json.dumps(ergebnis, ensure_ascii=False) if args.json else _bericht(ergebnis))
        return 0 if ergebnis["ok"] else 1

    eingabe = Path(args.eingabe)
    if not eingabe.is_file():
        print(f"Eingabe nicht gefunden: {eingabe}", file=sys.stderr)
        return 2

    if args.befehl == "docx":
        ziel = Path(args.ausgabe) if args.ausgabe else eingabe.with_suffix(".docx")
        erzeuge_docx(eingabe, ziel)
        print(f"Word-Dokument erstellt: {ziel}")
        return 0

    ziel = Path(args.ausgabe) if args.ausgabe else eingabe.with_suffix(".pdf")
    if eingabe.suffix.lower() not in (".md", ".markdown", ".txt", ".html", ".htm"):
        print("Eingabe muss Markdown (.md) oder HTML (.html) sein.", file=sys.stderr)
        return 2
    erzeuge_pdf(eingabe, ziel, titel=args.titel, fusszeile=args.fusszeile)
    ergebnis = pruefe_pdf(ziel)
    print(f"PDF erstellt: {ziel}")
    print(_bericht(ergebnis))
    return 0 if ergebnis["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
