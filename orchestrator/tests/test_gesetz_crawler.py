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
    parse_eu_regulation_xhtml,
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



# Gekuerztes, aber strukturell echtes Beispiel der EUR-Lex/CELLAR-ELI-XHTML-
# Struktur — verifiziert gegen den echten deutschen Text des EU AI Act,
# der DSGVO, des DSA und des Data Act (je 50-113 Artikel, dieselbe Form).
# Wichtig: "art_1.tit_1" ist die TITEL-Unterebene von Artikel 1, kein
# eigener Artikel — genau die Verwechslung, die der Praefix-Filter verhindert.
EU_BEISPIEL_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
<div class="eli-main-title" id="tit_1">
<p class="oj-doc-ti">VERORDNUNG (EU) 9999/1 DES EUROPÄISCHEN PARLAMENTS UND DES RATES</p>
<p class="oj-doc-ti">vom 1. Januar 2099</p>
<p class="oj-doc-ti">zur Regelung der Testsache</p>
</div>
<div class="eli-subdivision" id="art_1">
<p class="oj-ti-art">Artikel 1</p>
<div class="eli-title" id="art_1.tit_1"><p class="oj-sti-art">Gegenstand</p></div>
<div id="001.001"><p class="oj-normal">Dieser Artikel regelt die Testsache.</p></div>
</div>
<div class="eli-subdivision" id="art_2">
<p class="oj-ti-art">Artikel 2</p>
<div class="eli-title" id="art_2.tit_1"><p class="oj-sti-art">Begriffsbestimmungen</p></div>
<div id="002.001"><p class="oj-normal">(1) „Test“ bezeichnet diesen Testfall.</p></div>
<div id="002.002"><p class="oj-normal">(2) „Sache“ bezeichnet den Gegenstand.</p></div>
</div>
</body>
</html>"""

EU_LEER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
<div class="eli-main-title" id="tit_1"><p class="oj-doc-ti">Leere Verordnung</p></div>
</body>
</html>"""


class ParsingTheEuEliSchemaTests(unittest.TestCase):
    def test_the_title_is_assembled_from_all_lines(self):
        law = parse_eu_regulation_xhtml(EU_BEISPIEL_XHTML.encode(), "TestVO")
        self.assertIn("zur Regelung der Testsache", law["title"])

    def test_the_nested_article_title_div_is_not_a_second_article(self):
        """'art_1.tit_1' traegt einen Punkt im Id — genau das unterscheidet
        die Titel-Unterebene vom eigentlichen Artikel-Div."""
        law = parse_eu_regulation_xhtml(EU_BEISPIEL_XHTML.encode(), "TestVO")
        self.assertEqual(len(law["paragraphs"]), 2)
        self.assertEqual(law["paragraphs"][0]["enbez"], "Artikel 1")
        self.assertEqual(law["paragraphs"][1]["enbez"], "Artikel 2")

    def test_the_article_title_is_captured_separately(self):
        law = parse_eu_regulation_xhtml(EU_BEISPIEL_XHTML.encode(), "TestVO")
        self.assertEqual(law["paragraphs"][1]["titel"], "Begriffsbestimmungen")

    def test_multiple_absaetze_are_joined(self):
        law = parse_eu_regulation_xhtml(EU_BEISPIEL_XHTML.encode(), "TestVO")
        text = law["paragraphs"][1]["text"]
        self.assertIn("„Test“", text)
        self.assertIn("„Sache“", text)

    def test_the_heading_text_itself_is_not_duplicated_into_the_body(self):
        """oj-ti-art/oj-sti-art sind die Artikelnummer und ihr Titel — beide
        stehen schon in enbez/titel und duerfen nicht nochmal im Fliesstext
        auftauchen."""
        law = parse_eu_regulation_xhtml(EU_BEISPIEL_XHTML.encode(), "TestVO")
        self.assertNotIn("Artikel 1", law["paragraphs"][0]["text"])
        self.assertNotIn("Gegenstand", law["paragraphs"][0]["text"])

    def test_reuses_the_same_markdown_renderer(self):
        """Kein EU-eigener Renderer noetig — dieselbe Form wie parse_norm_xml."""
        law = parse_eu_regulation_xhtml(EU_BEISPIEL_XHTML.encode(), "TestVO")
        md = render_markdown(law)
        self.assertIn("## Artikel 2 Begriffsbestimmungen", md)

    def test_a_regulation_with_no_articles_yields_nothing(self):
        self.assertIsNone(parse_eu_regulation_xhtml(EU_LEER_XHTML.encode(), "LeerVO"))

    def test_the_fallback_name_is_the_jurabk(self):
        """Kein offizielles 'jurabk' in EUR-Lex-Dokumenten — der kuratierte
        Kurzname (z. B. 'EU-AI-Act') uebernimmt diese Rolle."""
        law = parse_eu_regulation_xhtml(EU_BEISPIEL_XHTML.encode(), "TestVO")
        self.assertEqual(law["jurabk"], "TestVO")


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


class AusfaelleBleibenNichtStummTests(unittest.TestCase):
    """#834: jeder Fehlschlag lag auf ``debug`` -- bei einem dauerhaften Mangel
    (kaputtes ``vault_chunks``-Schema) lief der Crawler endlos ohne Ergebnis
    und ohne eine einzige sichtbare Zeile. Eine Sammelmeldung am Ende macht
    genau diesen Zustand sichtbar, ohne den Normalfall zuzuspammen."""

    def test_ohne_ausfall_keine_meldung(self):
        from app.services.gesetz_crawler import _AusfallZaehler

        self.assertIsNone(_AusfallZaehler("Test").bericht(42))

    def test_meldung_nennt_anzahl_geschriebene_und_ersten_grund(self):
        from app.services.gesetz_crawler import _AusfallZaehler

        z = _AusfallZaehler("Test")
        z.melde("bgb", ValueError('column "embedding" of relation "vault_chunks" does not exist'))
        z.melde("stgb", RuntimeError("zweiter"))
        bericht = z.bericht(0)
        self.assertIn("2", bericht)
        self.assertIn("bgb", bericht)
        self.assertIn("embedding", bericht)
        # Nur der ERSTE Grund -- sonst waechst eine Logzeile auf tausende Normen.
        self.assertNotIn("zweiter", bericht)

    def test_ein_crawl_lauf_mit_lauter_fehlschlaegen_meldet_wirklich_eine_warnung(self):
        """VERHALTEN, nicht Quelltext: ein Zaehler, den niemand ausliest --
        oder eine Meldung hinter einer toten Verzweigung -- sieht im Quelltext
        richtig aus und bleibt trotzdem stumm. Deshalb der echte Lauf.

        Nachgebaut ist die Lage aus #834: jede Norm scheitert (dort am
        kaputten vault_chunks-Schema), der Lauf geht weiter, schreibt nichts.
        Kein Netz noetig -- der Fehler faellt vor dem ersten Abruf."""
        import asyncio
        from unittest.mock import patch

        from app.services.gesetz_crawler import GesetzCrawlerService

        dienst = GesetzCrawlerService()

        async def _toc(_selbst, _client):
            return [("Buergerliches Gesetzbuch", "bgb"), ("Strafgesetzbuch", "stgb")]

        async def _eine(_selbst, _client, slug):
            raise RuntimeError(f'column "embedding" of relation "vault_chunks" does not exist [{slug}]')

        with patch.object(GesetzCrawlerService, "_fetch_toc", _toc), \
                patch.object(GesetzCrawlerService, "_fetch_one", _eine):
            with self.assertLogs("app.services.gesetz_crawler", level="WARNING") as protokoll:
                geschrieben = asyncio.run(dienst.crawl())

        self.assertEqual(geschrieben, 0)
        gesamt = "\n".join(protokoll.output)
        self.assertIn("2", gesamt)
        self.assertIn("embedding", gesamt)

    def test_ein_lauf_ohne_fehlschlag_meldet_keine_warnung(self):
        """Gegenprobe: die Warnung darf nicht bei jedem Lauf kommen, sonst
        ist sie nach einer Woche Rauschen und niemand sieht sie mehr."""
        import asyncio
        import logging
        from unittest.mock import patch

        from app.services.gesetz_crawler import GesetzCrawlerService

        dienst = GesetzCrawlerService()

        async def _toc(_selbst, _client):
            return []

        with patch.object(GesetzCrawlerService, "_fetch_toc", _toc):
            with self.assertNoLogs("app.services.gesetz_crawler", level=logging.WARNING):
                self.assertEqual(asyncio.run(dienst.crawl()), 0)


class AusfaelleImEuLaufBleibenNichtStummTests(unittest.TestCase):
    """Dieselbe Pruefung wie oben fuer die ZWEITE Crawl-Schleife (``crawl_eu``).

    Beide Schleifen tragen denselben Mangel und dieselbe Reparatur; ohne
    eigenen Test kann die Sammelmeldung in dieser hier jederzeit wieder
    verschwinden, waehrend alle Tests gruen bleiben (#834)."""

    class _FehlerClient:
        """HTTP-Client, an dem JEDER Abruf scheitert -- kein Netz noetig."""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, *_a, **_k):
            raise RuntimeError(
                'column "embedding" of relation "vault_chunks" does not exist'
            )

    def test_ein_eu_lauf_mit_lauter_fehlschlaegen_meldet_wirklich_eine_warnung(self):
        import asyncio
        from unittest.mock import patch

        from app.services import gesetz_crawler as gc

        dienst = gc.GesetzCrawlerService()
        zwei = [("32016R0679", "DSGVO"), ("32019R0947", "Drohnen")]
        with patch.object(gc, "EU_CURATED_LAWS", zwei), \
                patch.object(gc.httpx, "AsyncClient", lambda *a, **k: self._FehlerClient()):
            with self.assertLogs("app.services.gesetz_crawler", level="WARNING") as protokoll:
                geschrieben = asyncio.run(dienst.crawl_eu())

        self.assertEqual(geschrieben, 0)
        gesamt = "\n".join(protokoll.output)
        self.assertIn("2", gesamt)
        self.assertIn("embedding", gesamt)
        self.assertIn("EU", gesamt)

    def test_ein_eu_lauf_ohne_fehlschlag_meldet_keine_warnung(self):
        import asyncio
        import logging
        from unittest.mock import patch

        from app.services import gesetz_crawler as gc

        dienst = gc.GesetzCrawlerService()
        with patch.object(gc, "EU_CURATED_LAWS", []), \
                patch.object(gc.httpx, "AsyncClient", lambda *a, **k: self._FehlerClient()):
            with self.assertNoLogs("app.services.gesetz_crawler", level=logging.WARNING):
                self.assertEqual(asyncio.run(dienst.crawl_eu()), 0)
