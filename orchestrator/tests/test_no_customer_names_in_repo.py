"""Kein Kundenname, kein Firmenname, kein Personenname im Quelltext.

Dieses Repo ist **oeffentlich**. Am 2026-08-13 fiel beim Durchsehen eines Pull
Requests auf, dass der Name eines Kunden an 34 Stellen in 20 Dateien stand — in
Kommentaren, Tests, im CHANGELOG, im Benutzerhandbuch. Die schlimmste Stelle war
keine Notiz, sondern die **Produkt-Oberflaeche**: in den Einstellungen standen die
internen Adressen dieses Kunden als Platzhalter (`mail.<kunde>.de`,
`svc-aiemployee@<kunde>.de`, eine interne IP). Die sah jeder Nutzer der Software —
auch jeder andere Kunde. Dazu der Nachname einer realen Ansprechperson in einem
Test.

Nichts davon war boese Absicht. Es entsteht beilaeufig: man schreibt auf, WO ein
Fehler auftrat, und der Ort hat nun einmal einen Namen. Genau deshalb reicht ein
Vorsatz nicht — es braucht eine Pruefung, die zusieht.

**Was stattdessen dahin gehoert:** „beim Kunden", „eine Kundenanlage", „der
Betreiber". Der Sachverhalt bleibt vollstaendig nachvollziehbar; nur der Name
faellt weg. Fuer Beispiele und Testdaten: `example.com` / `example.invalid`
(dafuer reserviert, RFC 2606) und Namen wie `m.mustermann`.

Der Ort eines Fehlers gehoert ins Projekt-Gedaechtnis, nicht ins oeffentliche
Repo — dort darf und soll der Klarname stehen.

Hinweis zur Reichweite: Dieser Test schuetzt den AKTUELLEN Stand. Was einmal
oeffentlich gepusht wurde, bleibt in der git-Historie und in fremden Klonen.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Namen, die hier nicht auftauchen duerfen. Beim naechsten Kunden erweitern —
#: das ist billiger als eine zweite Aufraeumaktion.
VERBOTEN = (
    "skbs",
    "klinikum",
    "braunschweig",
    "uhde",
)

#: Verzeichnisse ohne eigenen Quelltext.
UEBERSPRINGEN = {".git", "node_modules", ".next", "__pycache__", ".venv", "dist", "build"}

#: Diese Datei nennt die Namen zwangslaeufig — sie ist die Pruefung selbst.
AUSNAHMEN = {"orchestrator/tests/test_no_customer_names_in_repo.py"}

TEXTENDUNGEN = {
    ".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".yml", ".yaml",
    ".css", ".html", ".sh", ".toml", ".cfg", ".ini", ".txt", ".sql",
}

#: Ein eingebettetes Bild ist keine Prosa. Eine `data:...;base64,`-Nutzlast traegt
#: beliebige Bytes, und in genug Bytes steht irgendwann jede kurze Zeichenfolge.
#: Am 2026-08-24 enthielt ein eingebettetes Bild von 137 KB in einer einzigen Zeile
#: zwei Vierbuchstaben-Treffer rein zufaellig. Damit war diese Pruefung auf der
#: Hauptlinie dauerhaft rot — genau der Zustand, vor dem `_kandidaten()` oben warnt:
#: eine Wache, die immer schlaegt, wird abgeschaltet und faengt den echten Fall nicht
#: mehr.
#:
#: Herausgeschnitten wird nur die Nutzlast, nicht die Zeile. Steht der Name
#: daneben — im Dateinamen, im Alternativtext, im Kommentar dahinter — wird er
#: weiterhin gefunden.
BASE64_NUTZLAST = re.compile(r"data:[^;,\s]*;base64,[A-Za-z0-9+/=]*")


def _ohne_base64(zeile: str) -> str:
    return BASE64_NUTZLAST.sub("data:<bild>;base64,", zeile)


def _kandidaten():
    """Die Dateien, die tatsaechlich oeffentlich werden.

    Bis 2026-08-18 lief die Pruefung ueber das ganze Arbeitsverzeichnis. Damit
    schlug sie auch bei rein oertlichen Notizen an, die nie in git landen — ein
    ``todo.md`` mit dem Kundennamen im Wurzelverzeichnis genuegte, und die
    Pruefung war dauerhaft rot. Ein dauerhaft roter Test wird ignoriert, und
    dann faengt er den echten Fall nicht mehr.

    ``git ls-files`` liefert den Index: alles Eingecheckte UND alles bereits
    Vorgemerkte. Vormerken ist genau der Moment, in dem gewarnt werden muss —
    davor ist es eine private Notiz, danach ist es zu spaet.
    """
    try:
        roh = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT,
            capture_output=True, check=True, text=True, timeout=30,
        ).stdout
        pfade = [ROOT / n for n in roh.split("\0") if n]
    except (OSError, subprocess.SubprocessError):
        # Kein git zur Hand (z. B. im Docker-Bauzusammenhang) — dann lieber
        # alles pruefen als nichts.
        pfade = list(ROOT.rglob("*"))
    for p in pfade:
        if not p.is_file() or p.suffix.lower() not in TEXTENDUNGEN:
            continue
        if UEBERSPRINGEN & set(p.parts):
            continue
        if str(p.relative_to(ROOT)) in AUSNAHMEN:
            continue
        yield p


def _dateien():
    yield from _kandidaten()


class NoCustomerNamesTests(unittest.TestCase):
    def test_the_repo_names_no_customer(self):
        muster = re.compile("|".join(VERBOTEN), re.IGNORECASE)
        funde = []
        for p in _dateien():
            try:
                text = p.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            for nr, zeile in enumerate(text.splitlines(), 1):
                zeile = _ohne_base64(zeile)
                if muster.search(zeile):
                    funde.append(f"{p.relative_to(ROOT)}:{nr}: {zeile.strip()[:90]}")
        self.assertEqual(
            funde, [],
            "Kunden-/Personennamen im oeffentlichen Repo. Bitte durch 'beim Kunden' "
            "oder 'eine Kundenanlage' ersetzen; der Klarname gehoert ins "
            "Projekt-Gedaechtnis:\n" + "\n".join(funde[:40]),
        )


class TheGuardActuallyWorksTests(unittest.TestCase):
    """Eine Wache, die nichts findet, weil sie nirgends hinsieht, ist keine."""

    def test_it_looks_at_a_meaningful_number_of_files(self):
        self.assertGreater(len(list(_dateien())), 300)

    def test_it_covers_source_and_documents(self):
        endungen = {p.suffix for p in _dateien()}
        for noetig in (".py", ".tsx", ".md"):
            self.assertIn(noetig, endungen)

    def test_it_would_notice_a_new_occurrence(self):
        muster = re.compile("|".join(VERBOTEN), re.IGNORECASE)
        self.assertTrue(muster.search("Fehler trat bei SKBS auf"))
        self.assertTrue(muster.search("mail.klinikum-bs.de"))
        self.assertFalse(muster.search("Fehler trat bei einem Kunden auf"))

    def test_it_ignores_a_name_that_falls_out_of_an_embedded_image(self):
        """Zufall in Bilddaten ist kein Befund.

        Der Name wird hier aus ``VERBOTEN`` zusammengesetzt statt hingeschrieben —
        so waechst die Zahl der Klartextstellen im Repo nicht mit jedem neuen Test.
        """
        muster = re.compile("|".join(VERBOTEN), re.IGNORECASE)
        name = VERBOTEN[0]
        zeile = f'<img src="data:image/png;base64,AAAA{name}BBBB">'
        self.assertTrue(muster.search(zeile), "Vorbedingung: ungeschnitten faellt es auf")
        self.assertFalse(muster.search(_ohne_base64(zeile)))

    def test_it_still_notices_a_name_NEXT_TO_an_embedded_image(self):
        """Die Gegenprobe zum Ausschneiden — sonst waere eine Zeile mit Bild blind.

        Herausgeschnitten wird die Nutzlast, nicht die Zeile. Ein Name im
        Alternativtext davor oder im Kommentar dahinter muss weiter auffallen.
        """
        muster = re.compile("|".join(VERBOTEN), re.IGNORECASE)
        name = VERBOTEN[0]
        davor = f'<img alt="{name}" src="data:image/png;base64,AAAABBBB">'
        dahinter = f'<img src="data:image/png;base64,AAAABBBB"> <!-- {name} -->'
        self.assertTrue(muster.search(_ohne_base64(davor)))
        self.assertTrue(muster.search(_ohne_base64(dahinter)))


if __name__ == "__main__":
    unittest.main()
