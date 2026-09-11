"""Gesetze-Crawler — Norm-XML wird korrekt in durchsuchbares Markdown gefasst.

Kundenwunsch: Gesetzestexte crawlen, aktuell halten, semantisch durchsuchbar
machen. Die Quelle (gesetze-im-internet.de) liefert pro Gesetz eine XML-Datei
mit einem ``<dokumente>``-Wurzelelement: der erste ``<norm>`` traegt Titel/
Kurzbezeichnung, jeder weitere mit ``<enbez>`` ist ein Paragraph, ``<norm>``
ohne ``<enbez>`` sind reine Gliederungsueberschriften (Abschnitte) ohne
eigenen Text. Diese Struktur ist gegen ein echtes, von gesetze-im-internet.de
heruntergeladenes Beispiel verifiziert (1-DM-GoldmünzG, 19 Paragraphen).
"""

import unittest

from app.services.gesetz_crawler import (
    _slug_from_link,
    parse_norm_xml,
    render_markdown,
)

# Gekuerztes, aber strukturell echtes Beispiel — ein Gesetz mit einer
# Gliederungsueberschrift ohne Text (Abschnitt) und zwei echten Paragraphen,
# einer davon mit einer mehrzeiligen <langue> (das eigentliche Problem, das
# die Titelbereinigung behebt).
BEISPIEL_XML = """<?xml version="1.0" encoding="UTF-8" ?><!DOCTYPE dokumente SYSTEM "d.dtd">
<dokumente builddate="1" doknr="X"><norm builddate="1" doknr="X"><metadaten>
<jurabk>TestG</jurabk><ausfertigung-datum manuell="ja">2000-01-01</ausfertigung-datum>
<langue>Testgesetz ueber
die Sache</langue></metadaten><textdaten><text format="XML"><Content><P/></Content></text>
</textdaten></norm>
<norm builddate="1" doknr="XG1"><metadaten><jurabk>TestG</jurabk>
<gliederungseinheit><gliederungskennzahl>010</gliederungskennzahl>
<gliederungsbez>Erster Abschnitt</gliederungsbez></gliederungseinheit></metadaten>
<textdaten><text format="XML"><Content><P/></Content></text></textdaten></norm>
<norm builddate="1" doknr="XE1"><metadaten><jurabk>TestG</jurabk><enbez>§ 1</enbez>
<titel format="parat">Zweck</titel></metadaten><textdaten><text format="XML">
<Content><P>Dieses Gesetz regelt die Sache.</P></Content></text></textdaten></norm>
<norm builddate="1" doknr="XE2"><metadaten><jurabk>TestG</jurabk><enbez>§ 2</enbez>
<titel format="parat">Begriffe</titel></metadaten><textdaten><text format="XML">
<Content><P>(1) Die Sache im Sinne dieses Gesetzes ist alles.</P>
<P>(2) Ausnahmen regelt die Verordnung.</P></Content></text></textdaten></norm>
</dokumente>"""

LEERES_GESETZ_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<dokumente><norm><metadaten><jurabk>LeerG</jurabk><langue>Nichts drin</langue>
</metadaten><textdaten><text format="XML"><Content><P/></Content></text></textdaten></norm>
</dokumente>"""


class ParsingTheRealSchemaTests(unittest.TestCase):
    def test_jurabk_and_title_come_from_the_first_norm(self):
        law = parse_norm_xml(BEISPIEL_XML.encode())
        self.assertEqual(law["jurabk"], "TestG")
        self.assertEqual(law["title"], "Testgesetz ueber die Sache")

    def test_multiline_title_is_flattened_to_one_line(self):
        """Der eigentliche Fehlerfall: <langue> bricht im Quelltext ueber
        mehrere Zeilen um — unbereinigt zerreisst das die Markdown-
        Ueberschrift, die daraus wird."""
        law = parse_norm_xml(BEISPIEL_XML.encode())
        self.assertNotIn("\n", law["title"])

    def test_a_gliederungseinheit_produces_no_paragraph(self):
        """Ein Abschnittskopf hat kein <enbez> und keinen eigenen Text — er
        darf nicht als leerer Paragraph auftauchen."""
        law = parse_norm_xml(BEISPIEL_XML.encode())
        self.assertEqual(len(law["paragraphs"]), 2)
        self.assertEqual(law["paragraphs"][0]["enbez"], "§ 1")
        self.assertEqual(law["paragraphs"][1]["enbez"], "§ 2")

    def test_multiple_absaetze_in_one_paragraph_are_joined(self):
        law = parse_norm_xml(BEISPIEL_XML.encode())
        text = law["paragraphs"][1]["text"]
        self.assertIn("(1) Die Sache", text)
        self.assertIn("(2) Ausnahmen", text)

    def test_a_law_with_no_paragraphs_yields_nothing(self):
        """Nur ein Gesetzeskopf ohne jeden Paragraphentext (z. B. ein reiner
        Aufhebungs-Stub) waere ein leerer, nutzloser Indexeintrag."""
        self.assertIsNone(parse_norm_xml(LEERES_GESETZ_XML.encode()))


class RenderingToMarkdownTests(unittest.TestCase):
    def test_headings_carry_the_law_and_paragraph(self):
        """chunk_markdown spaltet nach Ueberschriften und traegt den
        Ueberschriftenpfad als Kontext in jeden Treffer — ohne "§ N" in der
        H2-Zeile waere ein Suchtreffer nicht auf seinen Paragraphen
        zurueckfuehrbar."""
        law = parse_norm_xml(BEISPIEL_XML.encode())
        md = render_markdown(law)
        self.assertIn("# TestG — Testgesetz ueber die Sache", md)
        self.assertIn("## § 1 Zweck", md)
        self.assertIn("## § 2 Begriffe", md)

    def test_the_actual_norm_text_is_present(self):
        law = parse_norm_xml(BEISPIEL_XML.encode())
        md = render_markdown(law)
        self.assertIn("Dieses Gesetz regelt die Sache.", md)


class SlugExtractionTests(unittest.TestCase):
    def test_the_stable_site_id_is_extracted(self):
        self.assertEqual(
            _slug_from_link("http://www.gesetze-im-internet.de/betaeubm_v/xml.zip"),
            "betaeubm_v",
        )

    def test_an_unrelated_link_yields_nothing(self):
        self.assertIsNone(_slug_from_link("http://example.invalid/not-a-law"))


if __name__ == "__main__":
    unittest.main()
