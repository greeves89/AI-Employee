"""Der PDF-Betrachter muss ``suspense={false}`` gesetzt lassen.

react-pdf 11 hat den Standard umgestellt: das Dokument laedt seither ueber
Suspense, und ein Ladefehler wird nicht mehr nur an ``onLoadError`` gemeldet,
sondern anschliessend an die naechste Fehlergrenze **geworfen**
(``dist/shared/hooks/useSuspenseResource.js``). Der Betrachter faengt Fehler
aber selbst ab und bietet dann den Download an — dieser Zweig waere tot, der
Nutzer saehe statt der PDF-eigenen Meldung nur noch die allgemeine Grenze aus
``file-preview``.

Die Zeile ist leicht zu uebersehen: ein Aufraeumen im Attributblock entfernt
sie, alles baut weiter gruen, und der Rueckschritt faellt erst auf, wenn
jemand eine beschaedigte Datei oeffnet. Genau diese Klasse hat am 18.08.2026
schon einmal die ganze Seite gerissen (siehe ``fehler-grenze.tsx``).

Geprueft wird im **Fenster** jedes ``<Document>``-Elements und erst, nachdem
Kommentare und Zeichenkettentexte entfernt sind: weder ein auskommentierter
noch ein anderswo im Modul stehender Treffer darf den Test gruen machen. Beim
ersten Entwurf dieser Wache fiel beides durch — ein *nachgestellter* Kommentar
(``options={…} // frueher suspense={false}``) ueberlebte, und ein zweites,
ungeschuetztes ``<Document>`` blieb unbemerkt. Deshalb die Umstaendlichkeit.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEWER = ROOT / "frontend/src/components/files/viewers/pdf-viewer.tsx"
PAKET = ROOT / "frontend/package.json"


def react_pdf_hauptversion() -> int:
    abhaengig = json.loads(PAKET.read_text())["dependencies"]["react-pdf"]
    return int(re.sub(r"^\D*", "", abhaengig).split(".")[0])


def nur_code(text: str) -> str:
    """Kommentare und Zeichenketten-INHALTE durch Leerraum ersetzen.

    Ein Regex auf ``^\s*//`` reicht nicht: ein nachgestellter Kommentar steht
    nicht am Zeilenanfang. Ein Regex auf ``//`` ueberall reicht auch nicht, er
    wuerde ``"https://…"`` zerschneiden. Also einmal zeichenweise durch, mit
    Blick darauf, ob man gerade in einer Zeichenkette steht. Die Anfuehrungs-
    zeichen selbst bleiben stehen, damit die Struktur erhalten bleibt.
    """
    aus = []
    i, n = 0, len(text)
    while i < n:
        z = text[i]
        if z in "\"'`":
            aus.append(z)
            i += 1
            while i < n and text[i] != z:
                i += 2 if text[i] == "\\" else 1
            aus.append(z)
            i += 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            ende = text.find("*/", i + 2)
            i = n if ende == -1 else ende + 2
            aus.append(" ")
        else:
            aus.append(z)
            i += 1
    return "".join(aus)


def oeffnende_document_tags(quelle: str) -> list[str]:
    """Jeder Bereich von ``<Document`` bis zum schliessenden ``>`` des Tags.

    Bewusst ALLE Vorkommen: wer spaeter eine zweite Ansicht danebenstellt (eine
    Miniaturleiste etwa), soll sie nicht ungeschuetzt einfuehren koennen.
    """
    tags = []
    for treffer in re.finditer(r"<Document(?![A-Za-z0-9_])", quelle):
        tiefe = 0
        for pos in range(treffer.start(), len(quelle)):
            zeichen = quelle[pos]
            if zeichen == "{":
                tiefe += 1
            elif zeichen == "}":
                tiefe -= 1
            elif zeichen == ">" and tiefe == 0:
                tags.append(quelle[treffer.start() : pos + 1])
                break
        else:
            raise AssertionError("Ein <Document>-Tag wird nicht geschlossen.")
    return tags


class DerBetrachterBleibtBeiSeinerEigenenFehlermeldungTests(unittest.TestCase):
    def setUp(self) -> None:
        if react_pdf_hauptversion() < 11:
            self.skipTest("Vor react-pdf 11 gibt es die Einstellung nicht.")
        self.tags = oeffnende_document_tags(nur_code(VIEWER.read_text()))
        self.assertTrue(self.tags, "Kein <Document> im PDF-Betrachter gefunden.")

    def test_suspense_ist_ueberall_abgeschaltet(self):
        for tag in self.tags:
            with self.subTest(tag=tag[:60]):
                self.assertIn("suspense={false}", tag)

    def test_der_eigene_fehlerzweig_existiert_ueberhaupt_noch(self):
        """Ohne ``onLoadError`` waere das Abschalten sinnlos — dann gaebe es
        keine eigene Behandlung, die Suspense verdraengen koennte."""
        for tag in self.tags:
            with self.subTest(tag=tag[:60]):
                self.assertIn("onLoadError", tag)
