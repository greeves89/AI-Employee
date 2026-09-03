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

import hashlib
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: SHA-256 der verbotenen Begriffe, kleingeschrieben.
#:
#: Frueher stand hier die Namensliste im KLARTEXT — in einer Datei, die durch
#: ihren Namen ankuendigt, dass dort Kundennamen zu finden sind, und die sich
#: selbst von der Pruefung ausnahm. Damit war ausgerechnet die Schutzmassnahme
#: das vollstaendigste, maschinenlesbare Kundenverzeichnis im oeffentlichen Repo,
#: und der Kommentar daneben lud dazu ein, es weiter zu pflegen (#688).
#:
#: Die Pruefung muss ERKENNEN, nicht ANZEIGEN — dafuer genuegt der Hash.
#:
#: Einen Begriff aufnehmen:
#:     python3 -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].lower().encode()).hexdigest())" BEGRIFF
#: und den Hash hier eintragen. Den Klartext ins Projekt-Gedaechtnis, nie hierher.
#:
#: Der letzte Eintrag ist ein Pruefbegriff ohne realen Bezug ('pruefbegriffnieecht'),
#: damit die Wache sich selbst testen kann, ohne einen echten Namen zu nennen.
VERBOTEN_HASHES = frozenset({
    "7a0cb6c5bcd6250ff3e7e4c70601559ef6fefc372ef3359775b6d88cf70859a4",
    "552dce9b46234fd85f680e980a0fe5c05c6e26458811abd6b9ba4d22fa97f82e",
    "730070c5a055c8faddd73ac3a12197aecebb464bbd1d30ff7c2bfcbfbf39e015",
    "ab935bbec91df64d64ec67fa71f49043490a0c0d900d7a80cf93355c4dbcd6bc",
    "7552f5a72c6fcc7bfb952d76e86481ed9c77f3b994d2c9613d926b72cdc7781e",
})

#: Ein Begriff ohne jeden realen Bezug, dessen Hash oben mitliegt. Die Tests
#: weiter unten pruefen damit die Wache selbst — frueher brauchten sie dafuer
#: einen echten Kundennamen im Quelltext.
PRUEFBEGRIFF = "pruefbegriffnieecht"

#: Woran eine Zeile in pruefbare Woerter zerfaellt. Der Preis der Hash-Loesung:
#: aus der Teilstring-Suche wird eine Wortsuche. Die praktisch relevanten Faelle
#: bleiben erfasst — `mail.<kunde>-bs.de` zerfaellt in Teile, von denen einer der
#: Name ist. Ein Name, der ohne Trennzeichen in einem laengeren Wort steckt,
#: wird nicht mehr gefunden; das ist der bewusste Tausch.
WORTTRENNER = re.compile(r"[^0-9A-Za-z_]+")


def _woerter(zeile: str):
    return (w for w in WORTTRENNER.split(zeile) if w)


def ist_verboten(wort: str) -> bool:
    """Ob dieses eine Wort auf der Liste steht — ohne sie preiszugeben."""
    return hashlib.sha256(wort.lower().encode()).hexdigest() in VERBOTEN_HASHES


def zeile_verboten(zeile: str) -> bool:
    return any(ist_verboten(w) for w in _woerter(zeile))


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


def _fundmeldung(pfad, nr: int) -> str:
    """Eine Zeile der Fundliste — Ort ohne Inhalt.

    Frueher stand der getroffene Text mit in der Meldung. Bei einem
    oeffentlichen Repo ist das CI-Protokoll aber ebenso oeffentlich: die Wache
    haette den Namen dort veroeffentlicht, den sie verhindern soll (#688). Wer
    den Fund behebt, hat die Datei ohnehin vor sich.
    """
    return f"{pfad}:{nr}"


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
        funde = []
        for p in _dateien():
            try:
                text = p.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            for nr, zeile in enumerate(text.splitlines(), 1):
                if zeile_verboten(_ohne_binaerdaten(zeile)):
                    funde.append(_fundmeldung(p.relative_to(ROOT), nr))
        self.assertEqual(
            funde, [],
            "Kunden-/Personennamen im oeffentlichen Repo. Bitte durch 'beim Kunden' "
            "oder 'eine Kundenanlage' ersetzen; der Klarname gehoert ins "
            "Projekt-Gedaechtnis. Der getroffene Begriff steht hier bewusst NICHT "
            "— er stuende sonst im CI-Protokoll, und das ist bei einem "
            "oeffentlichen Repo ebenso oeffentlich:\n" + "\n".join(funde[:40]),
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
        """Mit dem Pruefbegriff statt mit einem echten Namen — genau darum ging
        es bei #688."""
        self.assertTrue(zeile_verboten(f"Fehler trat bei {PRUEFBEGRIFF.upper()} auf"))
        self.assertTrue(zeile_verboten(f"mail.{PRUEFBEGRIFF}-bs.de"))
        self.assertTrue(zeile_verboten(f'svc-aiemployee@{PRUEFBEGRIFF}.de'))
        self.assertFalse(zeile_verboten("Fehler trat bei einem Kunden auf"))

    def test_grossschreibung_taeuscht_die_wache_nicht(self):
        for schreibweise in (PRUEFBEGRIFF.upper(), PRUEFBEGRIFF.title(), PRUEFBEGRIFF):
            self.assertTrue(zeile_verboten(f"Ort: {schreibweise}"))

    def test_die_liste_ist_kein_verzeichnis_mehr(self):
        """Die eigentliche Sache aus #688: keine Klartextnamen mehr in dieser
        Datei.

        Geprueft wird ueber die WERTE, nicht ueber einen Suchbegriff im
        Quelltext — ein `assertNotIn("VERBOTEN" + " = (")` haette sich selbst
        gefunden.
        """
        self.assertTrue(all(len(h) == 64 for h in VERBOTEN_HASHES))
        self.assertNotIn("VERBOTEN", dir())

    def test_die_hashes_sehen_aus_wie_hashes(self):
        for h in VERBOTEN_HASHES:
            self.assertRegex(h, r"^[0-9a-f]{64}$")

    def test_die_fundmeldung_nennt_den_begriff_nicht(self):
        """Sonst stuende der Name im CI-Protokoll — bei einem oeffentlichen
        Repo genauso oeffentlich wie im Quelltext."""
        meldung = _fundmeldung("beispiel/datei.py", 42)
        self.assertEqual(meldung, "beispiel/datei.py:42")


class Base64NutzlastTests(unittest.TestCase):
    """Bilddaten sind kein Text — und der Text daneben bleibt trotzdem geprueft.

    Gearbeitet wird mit ``PRUEFBEGRIFF`` — einem Wort ohne realen Bezug, dessen
    Hash mit in der Liste liegt. Frueher lasen diese Tests einen echten
    Kundennamen aus der Liste; damit war die Wache selbst eine Fundstelle
    (#688).
    """

    def setUp(self):
        self.name = PRUEFBEGRIFF

    def test_zufallstreffer_im_bild_zaehlt_nicht(self):
        """Base64 nutzt ``+`` und ``/`` — beides Worttrenner. Ein Name kann dort
        also weiterhin zufaellig als eigenstaendiges Wort entstehen, nur seltener
        als bei der frueheren Teilstring-Suche. Genau dafuer bleibt das
        Ausschneiden der Nutzlast noetig: am 2026-08-24 stand die Pruefung
        deswegen auf ``main`` rot und blockierte alle vier offenen Anfragen,
        ohne dass irgendwo ein Name stand.
        """
        zeile = f'<img src="data:image/jpeg;base64,QUJD+{self.name}/RUZH==">'
        self.assertTrue(
            zeile_verboten(zeile),
            "Vorbedingung: ungeschnitten muss diese Zeile auffallen — sonst "
            "prueft der Test nicht das Ausschneiden, sondern gar nichts.",
        )
        self.assertFalse(zeile_verboten(_ohne_binaerdaten(zeile)))

    def test_ein_name_ohne_trennzeichen_im_wort_faellt_durch(self):
        """Der bewusste Tausch der Hash-Loesung, hier festgehalten statt
        verschwiegen: die Wortsuche findet keinen Namen mehr, der ohne
        Trennzeichen in einem laengeren Wort steckt. Wer das aendern will, muesste
        den Klartext zurueckholen — und damit #688 rueckgaengig machen.
        """
        self.assertFalse(zeile_verboten(f"XX{self.name}YY"))
        self.assertTrue(zeile_verboten(f"XX {self.name} YY"))
        self.assertTrue(zeile_verboten(f"XX-{self.name}-YY"))

    def test_nur_die_nutzlast_faellt_weg(self):
        davor = f'<img alt="{self.name}" src="data:image/png;base64,QUJDRA==">'
        dahinter = f'<img src="data:image/png;base64,QUJDRA=="> <!-- {self.name} -->'
        self.assertTrue(zeile_verboten(_ohne_binaerdaten(davor)))
        self.assertTrue(zeile_verboten(_ohne_binaerdaten(dahinter)))

    def test_ein_fund_hinter_grossen_bilddaten_wird_trotzdem_erkannt(self):
        """Frueher pruefte hier ein Test, ob der Name in der Fundmeldung
        sichtbar bleibt. Seit #688 steht er dort bewusst NICHT mehr — das
        CI-Protokoll eines oeffentlichen Repos ist ebenso oeffentlich. Was
        bleibt, ist die eigentliche Anforderung: der Fund darf nicht verloren
        gehen, nur weil 137 KB Bilddaten davor stehen.
        """
        fuellung = "QUJDRA==" * 200
        zeile = f'<img src="data:image/png;base64,{fuellung}"> <!-- {self.name} -->'
        self.assertTrue(zeile_verboten(_ohne_binaerdaten(zeile)))
        self.assertEqual(_fundmeldung("bild.html", 556), "bild.html:556")

    def test_echter_fund_im_fliesstext_bleibt_ein_fund(self):
        zeile = f"Fehler trat bei {self.name.upper()} auf"
        self.assertTrue(zeile_verboten(_ohne_binaerdaten(zeile)))

    def test_bildverweis_ohne_nutzlast_bleibt_unangetastet(self):
        zeile = f'<img src="/bilder/{self.name}.png">'
        self.assertEqual(_ohne_binaerdaten(zeile), zeile)


if __name__ == "__main__":
    unittest.main()
