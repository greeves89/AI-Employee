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

#: Eingebettete Binaerdaten (``data:...;base64,...``) sind keine lesbare
#: Sprache. In einem 140 KB grossen Bild trifft ein vierstelliger Name mit
#: hoher Wahrscheinlichkeit zufaellig zu — am 2026-08-24 stand genau deshalb
#: die Pruefung auf ``main`` rot und blockierte alle vier offenen Pull
#: Requests, ohne dass irgendwo ein Name stand. Ein dauerhaft roter Test wird
#: ignoriert, und dann faengt er den echten Fall nicht mehr.
BASE64_NUTZLAST = re.compile(r"data:[^;,\s\"']*;base64,[A-Za-z0-9+/=]+")


def _ohne_binaerdaten(zeile: str) -> str:
    """Nur die Nutzlast faellt weg — der Text drumherum wird weiter geprueft.

    Das Ergebnis geht auch in die Fundmeldung. Eine Zeile mit eingebettetem
    Bild ist schnell 137 KB lang; ungeschnitten besteht die auf 90 Zeichen
    gekuerzte Meldung nur aus Bilddaten, und der Name, um den es geht, faellt
    hinten heraus. Wer die Meldung liest, muss sehen, was getroffen hat.
    """
    return BASE64_NUTZLAST.sub("", zeile)


def _fundmeldung(pfad, nr: int, zeile: str) -> str:
    """Eine Zeile der Fundliste — geschnitten, damit der Name sichtbar bleibt."""
    return f"{pfad}:{nr}: {_ohne_binaerdaten(zeile).strip()[:90]}"


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
                if muster.search(_ohne_binaerdaten(zeile)):
                    funde.append(_fundmeldung(p.relative_to(ROOT), nr, zeile))
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


class Base64NutzlastTests(unittest.TestCase):
    """Bilddaten sind kein Text — und der Text daneben bleibt trotzdem geprueft.

    Die Namen werden hier absichtlich aus ``VERBOTEN`` gelesen statt
    hingeschrieben: so kommt durch diese Tests keine weitere Fundstelle ins
    Repo, und beim naechsten Kunden wachsen sie von allein mit.
    """

    def setUp(self):
        self.muster = re.compile("|".join(VERBOTEN), re.IGNORECASE)
        self.name = VERBOTEN[0]

    def test_zufallstreffer_im_bild_zaehlt_nicht(self):
        zeile = f'<img src="data:image/jpeg;base64,QUJD{self.name}RUZH==">'
        self.assertTrue(
            self.muster.search(zeile),
            "Vorbedingung: ungeschnitten muss diese Zeile auffallen — sonst "
            "prueft der Test nicht das Ausschneiden, sondern gar nichts.",
        )
        self.assertFalse(self.muster.search(_ohne_binaerdaten(zeile)))

    def test_nur_die_nutzlast_faellt_weg(self):
        davor = f'<img alt="{self.name}" src="data:image/png;base64,QUJDRA==">'
        dahinter = f'<img src="data:image/png;base64,QUJDRA=="> <!-- {self.name} -->'
        self.assertTrue(self.muster.search(_ohne_binaerdaten(davor)))
        self.assertTrue(self.muster.search(_ohne_binaerdaten(dahinter)))

    def test_die_fundmeldung_zeigt_den_namen_statt_der_bilddaten(self):
        """Sonst wird aus einem lauten Fehler ein unlesbarer.

        Der Name steht hier hinter der Nutzlast — genau die Stelle, die die
        Kuerzung auf 90 Zeichen verschluckt. Stuende er davor, waere er auch
        ungeschnitten sichtbar und der Test bewiese nichts.
        """
        fuellung = "QUJDRA==" * 200
        zeile = f'<img src="data:image/png;base64,{fuellung}"> <!-- {self.name} -->'
        self.assertNotIn(
            self.name.lower(), zeile.strip()[:90].lower(),
            "Vorbedingung: ungeschnitten muss der Name aus der Meldung fallen.",
        )
        self.assertIn(self.name.lower(), _fundmeldung("bild.html", 556, zeile).lower())

    def test_echter_fund_im_fliesstext_bleibt_ein_fund(self):
        zeile = f"Fehler trat bei {self.name.upper()} auf"
        self.assertTrue(self.muster.search(_ohne_binaerdaten(zeile)))

    def test_bildverweis_ohne_nutzlast_bleibt_unangetastet(self):
        zeile = f'<img src="/bilder/{self.name}.png">'
        self.assertEqual(_ohne_binaerdaten(zeile), zeile)


if __name__ == "__main__":
    unittest.main()
