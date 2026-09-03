"""Eine Vorlage muss sagen, WOFUER der Agent da ist.

Vorgabe des Nutzers (16.08.2026): „Schau in alle Templates rein. Sind die
sinnvoll und ergeben Sinn bzgl. des Onboardings? Das Template soll mitgeben
WOFUER der Agent da ist."

Beim Durchsehen der 31 mitgelieferten Vorlagen zeigten sich vier Luecken:

1. **15 Vorlagen bestanden nur aus einer Technologieliste** — 190 bis 320
   Zeichen eigener Inhalt unter der Ueberschrift „Core Expertise", sonst nichts.
   Sie sagten, was der Agent KENNT, nicht wofuer er da ist, was er abliefert
   oder wann er fertig ist. Zum Vergleich: die ausgearbeiteten Vorlagen hatten
   1.500 bis 2.400 Zeichen mit Arbeitsweise, Zusammenarbeit und Ablage.
2. **Keine einzige Vorlage** — auch keine der ausgearbeiteten — nannte den
   Zweck oder ein Kriterium fuer „fertig".
3. **26 von 31 Beschreibungen waren englisch**, in einer deutschen Oberflaeche.
   Die Beschreibung ist der eine Satz, den ein Anwender beim Anlegen liest.
4. Die Kategorien ``productivity`` und ``finance`` hatten im Frontend keine
   Bezeichnung — drei Kacheln zeigten den rohen englischen Schluessel.

Diese Pruefungen halten den Zustand. Sie laufen ueber ALLE Vorlagen, damit eine
neue nicht wieder als blosse Stichwortliste hereinkommt.
"""

import re
import unittest
from pathlib import Path

from app.core.agent_templates import _PLATFORM_SECTION, BUILTIN_TEMPLATES

ROOT = Path(__file__).resolve().parents[2]
MODAL = (ROOT / "frontend/src/components/agents/create-agent-modal.tsx").read_text()


def _eigener_teil(t: dict) -> str:
    """Der rollenspezifische Teil — ohne den Block, den alle teilen."""
    return (t.get("knowledge_template") or "").replace(_PLATFORM_SECTION, "")


class EveryTemplateStatesItsPurposeTests(unittest.TestCase):
    def test_each_one_says_what_it_is_for(self):
        for t in BUILTIN_TEMPLATES:
            with self.subTest(vorlage=t["name"]):
                self.assertIn("### Wofuer du da bist", _eigener_teil(t))

    def test_each_one_says_when_it_is_done(self):
        """Ohne Abnahmekriterium meldet ein Agent „erledigt", sobald er geredet
        hat — genau der Fehler, der am selben Tag in der Sprachfront auffiel."""
        for t in BUILTIN_TEMPLATES:
            with self.subTest(vorlage=t["name"]):
                self.assertIn("**Fertig heisst:**", _eigener_teil(t))

    def test_the_purpose_stands_before_the_details(self):
        """Weiter unten liest es niemand — auch kein Modell mit begrenztem
        Blick auf einen langen Text."""
        for t in BUILTIN_TEMPLATES:
            with self.subTest(vorlage=t["name"]):
                eigen = _eigener_teil(t)
                self.assertLess(eigen.index("### Wofuer du da bist"), 400)


class NoTemplateIsJustAKeywordListTests(unittest.TestCase):
    """15 Vorlagen hatten 190-320 Zeichen: eine Aufzaehlung von Technologien.
    Daraus kann weder ein Anwender noch ein Modell einen Auftrag ableiten."""

    MINDESTLAENGE = 600

    def test_every_template_carries_enough_substance(self):
        for t in BUILTIN_TEMPLATES:
            with self.subTest(vorlage=t["name"]):
                self.assertGreaterEqual(len(_eigener_teil(t)), self.MINDESTLAENGE)

    def test_every_template_describes_how_to_work(self):
        """Kernkompetenzen allein sind ein Lebenslauf, keine Arbeitsanweisung."""
        for t in BUILTIN_TEMPLATES:
            with self.subTest(vorlage=t["name"]):
                eigen = _eigener_teil(t).lower()
                self.assertTrue(
                    any(k in eigen for k in ("arbeitsweise", "working principles",
                                             "prinzipien", "ablauf", "core loop")),
                    "keine Arbeitsweise beschrieben",
                )

    def test_every_template_names_where_its_output_goes(self):
        """Ohne Ablageort landen Ergebnisse irgendwo und der Nutzer findet sie
        nicht.

        Geprueft wird die ABSICHT — „der Nutzer weiss, wo das Ergebnis liegt" —
        nicht ein bestimmter Pfad. Die meisten Vorlagen legen im Arbeitsbereich
        ab; manche schreiben bewusst nach draussen (Aufgabenliste, Kalender,
        Wissensdatenbank). Als der erste solche Fall dazukam, verlangte dieser
        Test noch einen `/workspace/`-Pfad und haette die Vorlage gezwungen,
        einen Ablageort zu erfinden, den sie gar nicht benutzt.
        """
        # Ein Ziel ausserhalb zaehlt, wenn es beim Namen genannt ist.
        AUSSERHALB = ("meine aufgaben", "ms_create_task", "kalender",
                      "write_knowledge", "wissensdatenbank", "second brain")
        for t in BUILTIN_TEMPLATES:
            with self.subTest(vorlage=t["name"]):
                eigen = _eigener_teil(t)
                self.assertTrue(
                    "/workspace/" in eigen or any(z in eigen.lower() for z in AUSSERHALB),
                    "kein Ablageort genannt — weder im Arbeitsbereich noch draussen",
                )


class WhatTheUserReadsIsGermanTests(unittest.TestCase):
    """Anzeigename und Beschreibung stehen in der Auswahl beim Anlegen eines
    Agenten — in einer durchgehend deutschen Oberflaeche."""

    #: Woerter, die in einer deutschen Beschreibung nichts verloren haben.
    #: Bewusst nur eindeutige Faelle: Fachbegriffe wie „Web" oder „API" sind in
    #: einem deutschen Satz voellig in Ordnung.
    ENGLISCH = re.compile(
        r"\b(creates?|writes?|builds?|manages?|reviews?|analyzes?|"
        r"answers?|researches|conducts?|delegates?|translates?|crawls?|and|for|"
        # „Design" NICHT: das Wort ist im Deutschen gebraeuchlich
        # („Design-System"), und ein Treffer darauf waere ein falscher Alarm.
        r"with|the)\b", re.IGNORECASE)

    def test_no_description_is_english(self):
        for t in BUILTIN_TEMPLATES:
            with self.subTest(vorlage=t["name"]):
                treffer = self.ENGLISCH.findall(t["description"])
                self.assertEqual(treffer, [], f"englisch in: {t['description']!r}")

    def test_every_description_is_a_full_thought(self):
        for t in BUILTIN_TEMPLATES:
            with self.subTest(vorlage=t["name"]):
                self.assertGreaterEqual(len(t["description"]), 40)

    def test_descriptions_stay_short_enough_for_the_tile(self):
        """Die Kachel schneidet nach zwei Zeilen ab (``line-clamp-2``)."""
        for t in BUILTIN_TEMPLATES:
            with self.subTest(vorlage=t["name"]):
                self.assertLessEqual(len(t["description"]), 110)


class EveryCategoryHasALabelTests(unittest.TestCase):
    """Zwei Listen, die niemand gegeneinander gehalten hat — dasselbe Muster,
    das am selben Tag einen Codex-Agenten komplett lahmgelegt hat."""

    BEKANNT = set(re.findall(
        r"^\s*(\w+):",
        MODAL.split("CATEGORY_LABELS: Record<string, string> = {", 1)[1].split("};", 1)[0],
        re.M))
    FARBEN = set(re.findall(
        r"^\s*(\w+):",
        MODAL.split("CATEGORY_COLORS: Record<string, string> = {", 1)[1].split("};", 1)[0],
        re.M))

    def test_every_used_category_has_a_german_label(self):
        for t in BUILTIN_TEMPLATES:
            with self.subTest(vorlage=t["name"]):
                self.assertIn(t["category"], self.BEKANNT)

    def test_every_used_category_has_a_colour(self):
        for t in BUILTIN_TEMPLATES:
            with self.subTest(vorlage=t["name"]):
                self.assertIn(t["category"], self.FARBEN)


class TheTemplateStaysUsableTests(unittest.TestCase):
    """Kleinigkeiten, die beim Umschreiben leicht kaputtgehen."""

    def test_the_shared_platform_block_is_still_attached(self):
        """Ohne ihn kennt der Agent seine Werkzeuge nicht."""
        for t in BUILTIN_TEMPLATES:
            if t["name"] == "os-agent":
                continue   # bringt eine eigene, vollstaendige Anleitung mit
            with self.subTest(vorlage=t["name"]):
                self.assertIn(_PLATFORM_SECTION, t["knowledge_template"])

    def test_names_are_unique(self):
        namen = [t["name"] for t in BUILTIN_TEMPLATES]
        self.assertEqual(len(namen), len(set(namen)))

    def test_no_emoji_in_what_the_user_reads(self):
        """Harte Vorgabe des Nutzers: keine Emojis in der Oberflaeche."""
        for t in BUILTIN_TEMPLATES:
            with self.subTest(vorlage=t["name"]):
                for zeichen in t["display_name"] + t["description"]:
                    self.assertLess(ord(zeichen), 0x2190, f"Emoji: {zeichen!r}")


if __name__ == "__main__":
    unittest.main()
