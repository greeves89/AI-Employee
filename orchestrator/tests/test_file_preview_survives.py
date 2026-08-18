"""Eine kaputte Datei darf nicht die ganze Seite mitreissen.

Nutzerbericht vom 18.08.2026, mit Bildschirmfoto: „Eine Datei brachte mich zu
diesem Fehler" — die Agentenseite zeigte nur noch „This page couldn't load".
In der Konsole:

    TypeError: null is not an object (evaluating 'this.messageHandler.sendWithPromise')

Das ist pdf.js ohne Arbeiter. Der wurde von ``//unpkg.com`` geladen — ein
Browser darf einen Worker aber nicht von einem fremden Ursprung starten. Und
weil es im GESAMTEN Frontend keine einzige Fehlergrenze gab, kippte React den
kompletten Baum: der Nutzer verlor nicht die Vorschau, sondern den Agenten, an
dem er gerade arbeitete.

Nebenbei war der CDN-Bezug auch inhaltlich falsch: die Anlage laeuft selbst
gehostet (auch abgeschottet), und jedes geoeffnete PDF haette einem
Fremdanbieter verraten, dass es geoeffnet wurde.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PDF = (ROOT / "frontend/src/components/files/viewers/pdf-viewer.tsx").read_text()
#: Ohne Kommentarzeilen — der Hergang steht dort woertlich drin, samt des
#: Hostnamens, den es aus dem CODE zu verbannen gilt.
PDF_CODE = "\n".join(
    z for z in PDF.splitlines() if not z.lstrip().startswith(("//", "*", "/*"))
)
VORSCHAU = (ROOT / "frontend/src/components/files/file-preview.tsx").read_text()
GRENZE = (ROOT / "frontend/src/components/ui/fehler-grenze.tsx").read_text()
KOPIER = (ROOT / "frontend/scripts/copy-pdf-assets.mjs").read_text()
PAKET = (ROOT / "frontend/package.json").read_text()


class NothingIsFetchedFromAForeignHostTests(unittest.TestCase):
    def test_the_worker_no_longer_comes_from_a_cdn(self):
        self.assertNotIn("unpkg.com", PDF_CODE)

    def test_the_worker_is_bundled_from_node_modules(self):
        self.assertIn("pdfjs-dist/build/pdf.worker.min.mjs", PDF_CODE)
        self.assertIn("import.meta.url", PDF_CODE)

    def test_the_extra_data_is_served_from_our_own_origin(self):
        self.assertIn('cMapUrl: "/pdfjs/cmaps/"', PDF)
        self.assertIn('standardFontDataUrl: "/pdfjs/standard_fonts/"', PDF)

    def test_that_extra_data_is_actually_copied_at_build_time(self):
        """Ohne diesen Schritt zeigen die Pfade oben ins Leere."""
        self.assertIn("prebuild", PAKET)
        self.assertIn("copy-pdf-assets.mjs", PAKET)
        for teil in ("cmaps", "standard_fonts"):
            self.assertIn(teil, KOPIER)


class OneBrokenPartDoesNotKillThePageTests(unittest.TestCase):
    def test_a_boundary_exists_at_all(self):
        """Vorher gab es im ganzen Frontend keine einzige."""
        self.assertIn("getDerivedStateFromError", GRENZE)

    def test_the_preview_sits_inside_it(self):
        self.assertIn("<FehlerGrenze", VORSCHAU)
        self.assertIn("<FilePreviewInner", VORSCHAU)

    def test_the_boundary_is_in_the_shared_component_not_in_the_pages(self):
        """Es gibt zwei Dateibaeume. Saesse die Grenze in den Seiten, waere die
        zweite die naechste vergessene Stelle."""
        for seite in ("frontend/src/app/files/page.tsx", "frontend/src/app/agents/[id]/page.tsx"):
            with self.subTest(seite=seite):
                self.assertNotIn("FehlerGrenze", (ROOT / seite).read_text())

    def test_choosing_another_file_clears_the_error(self):
        """Sonst bliebe die Meldung stehen, bis die Seite neu geladen wird."""
        self.assertIn("schluessel={props.filePath}", VORSCHAU)
        self.assertIn("componentDidUpdate", GRENZE)

    def test_a_pdf_that_cannot_be_read_says_so_and_offers_the_download(self):
        self.assertIn("PDF kann nicht angezeigt werden", PDF)
        self.assertIn("Stattdessen herunterladen", PDF)


if __name__ == "__main__":
    unittest.main()
