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


def _dateien():
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in TEXTENDUNGEN:
            continue
        if UEBERSPRINGEN & set(p.parts):
            continue
        if str(p.relative_to(ROOT)) in AUSNAHMEN:
            continue
        yield p


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


if __name__ == "__main__":
    unittest.main()
