"""Lesbaren Text aus Dokumenten holen (PDF, Word, Excel) — EINE Stelle dafuer.

Genutzt von den OneDrive-Werkzeugen (``msgraph_mcp``), der Sprachfront und den
Projektdateien (``projekt_dateien``). Bewusst schlank und vollstaendig defensiv:
eine kaputte oder verschluesselte Datei endet in einer klaren Meldung, nie in
einem Absturz.
"""

import io
import logging
import zipfile

from app.core.log_redaction import scrub_log

logger = logging.getLogger(__name__)

#: Grenzen gegen präparierte Dateien (gemessen: eine 67-KB-.docx, die auf 20 MB
#: XML aufgeht, brauchte 88 s und 877 MB RAM).
MAX_ENTPACKT = 50 * 1024 * 1024     # Summe aller Einträge eines Word-/Excel-Archivs
MAX_EINTRAEGE = 2000                # Einträge im Archiv
MAX_PDF_SEITEN = 300
MAX_ZEILEN = 50_000                 # Excel-Zeilen über alle Blätter

#: Meldungen statt Text — der Aufrufer erkennt daran „nichts ausgelesen“.
MELDUNGEN = ("Das PDF ist passwortgeschützt", "Das PDF enthält keinen extrahierbaren Text",
             "Das Word-Dokument enthält keinen Text", "Die Excel-Datei enthält keine Daten",
             "Inhalt konnte nicht extrahiert werden", "Die Datei ist zu groß zum Auslesen")


def ist_meldung(text: str | None) -> bool:
    return bool(text) and text.startswith(MELDUNGEN)


def _archiv_pruefen(raw: bytes) -> str | None:
    """Word/Excel sind ZIP-Archive: vor dem Öffnen die entpackte Größe prüfen."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            eintraege = z.infolist()
    except zipfile.BadZipFile:
        return "Inhalt konnte nicht extrahiert werden (kein gültiges Archiv)."
    if len(eintraege) > MAX_EINTRAEGE or sum(e.file_size for e in eintraege) > MAX_ENTPACKT:
        return "Die Datei ist zu groß zum Auslesen (entpackt über 50 MB)."
    return None


def text_auslesen(raw: bytes, filename: str, content_type: str, max_chars: int = 12000) -> str | None:
    """Extract readable text from a downloaded document (PDF/Word/Excel).

    Returns the text, or None if the format isn't a supported document (caller then
    falls back to plain-text decoding). Kept dependency-light and fully defensive —
    a broken/encrypted file must degrade to a clear message, never crash the tool.
    """
    name = (filename or "").lower()
    ct = (content_type or "").lower()

    def _clip(t: str) -> str:
        t = (t or "").strip()
        return t[:max_chars] if t else ""

    try:
        if name.endswith(".pdf") or "application/pdf" in ct:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            if getattr(reader, "is_encrypted", False):
                try:
                    reader.decrypt("")
                except Exception:  # noqa: BLE001
                    return "Das PDF ist passwortgeschützt — Inhalt nicht lesbar."
            parts, laenge = [], 0
            for nr, page in enumerate(reader.pages):
                if nr >= MAX_PDF_SEITEN:
                    break
                stueck = page.extract_text() or ""
                parts.append(stueck)
                laenge += len(stueck)
                if laenge > max_chars:
                    break
            text = _clip("\n".join(parts))
            return text or ("Das PDF enthält keinen extrahierbaren Text (evtl. ein "
                            "gescanntes Bild ohne OCR).")

        if name.endswith(".docx") or "wordprocessingml" in ct:
            from docx import Document
            fehler = _archiv_pruefen(raw)
            if fehler:
                return fehler
            doc = Document(io.BytesIO(raw))
            parts = [p.text for p in doc.paragraphs if p.text]
            for table in doc.tables:
                for row in table.rows:
                    parts.append("\t".join(c.text for c in row.cells))
            return _clip("\n".join(parts)) or "Das Word-Dokument enthält keinen Text."

        if name.endswith(".xlsx") or "spreadsheetml" in ct:
            from openpyxl import load_workbook
            fehler = _archiv_pruefen(raw)
            if fehler:
                return fehler
            wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            parts, laenge, zeilen = [], 0, 0
            for ws in wb.worksheets:
                parts.append(f"# {ws.title}")
                # Laufende Zähler: die Summe je Zeile neu zu bilden war quadratisch
                # (40.000 kurze Zeilen: 39 s auf dem Mac).
                for row in ws.iter_rows(values_only=True):
                    zeilen += 1
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        zeile = "\t".join(cells)
                        parts.append(zeile)
                        laenge += len(zeile) + 1
                    if laenge > max_chars or zeilen >= MAX_ZEILEN:
                        break
                if laenge > max_chars or zeilen >= MAX_ZEILEN:
                    break
            return _clip("\n".join(parts)) or "Die Excel-Datei enthält keine Daten."
    except Exception as e:  # noqa: BLE001
        logger.warning("document extraction failed for %s: %s", scrub_log(filename), scrub_log(e))
        return f"Inhalt konnte nicht extrahiert werden ({type(e).__name__})."
    return None  # not a recognized document format → caller tries plain text
