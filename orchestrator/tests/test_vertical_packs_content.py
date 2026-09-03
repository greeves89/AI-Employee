"""Branchen-Pakete: Inhalte, und der stille Fehler dahinter (#395).

Ein Paket verweist über **Namen** auf eingebaute Vorlagen. Stimmt ein Name nicht,
schlägt die Einrichtung nicht laut fehl — sie legt einfach einen Agenten weniger
an. Der Nutzer bekommt ein halbes Team und merkt es erst, wenn er den fehlenden
Agenten sucht. Der erste Test hier ist deshalb wichtiger als er aussieht.

Neu in diesem Stand: **Steuerkanzlei** und **Handwerksbetrieb**, samt der vier
Vorlagen, die sie brauchen (Buchhaltung, Lohnbuchhaltung, Angebot & Kalkulation,
Disposition).

Der DATEV-Export (#393) ist pausiert — die Steuerkanzlei ist bewusst so gebaut,
dass sie ohne ihn nutzbar ist: Vorkontierung, Fristen und Belegprüfung stehen für
sich, nur die Ausgabe als Buchungsstapel fehlt.
"""

import unittest

from app.core.agent_templates import BUILTIN_TEMPLATES
from app.core.vertical_packs import BUILTIN_VERTICAL_PACKS, get_pack

TEMPLATE_NAMES = {t["name"] for t in BUILTIN_TEMPLATES}


class EveryPackCanActuallyBeProvisionedTests(unittest.TestCase):
    def test_every_referenced_template_exists(self):
        """Der stille Fehler: ein Tippfehler im Namen kostet einen Agenten, ohne
        dass irgendetwas rot wird."""
        for pack in BUILTIN_VERTICAL_PACKS:
            for name in pack["template_names"]:
                with self.subTest(pack=pack["slug"], template=name):
                    self.assertIn(name, TEMPLATE_NAMES)

    def test_slugs_are_unique(self):
        slugs = [p["slug"] for p in BUILTIN_VERTICAL_PACKS]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_get_pack_finds_the_new_ones(self):
        for slug in ("steuerkanzlei", "handwerksbetrieb"):
            with self.subTest(slug):
                self.assertIsNotNone(get_pack(slug))

    def test_get_pack_is_honest_about_unknown_slugs(self):
        self.assertIsNone(get_pack("gibt-es-nicht"))

    def test_every_pack_brings_more_than_one_agent(self):
        """Ein Paket mit einem Agenten ist kein Team — dafuer braucht niemand ein
        Paket."""
        for pack in BUILTIN_VERTICAL_PACKS:
            with self.subTest(pack["slug"]):
                self.assertGreaterEqual(len(pack["template_names"]), 2)

    def test_every_pack_has_knowledge_and_a_demo(self):
        """Ohne Startwissen ist ein Paket nur eine Agentenliste, und ohne erste
        Aufgabe steht der Nutzer vor einem leeren Bildschirm."""
        for pack in BUILTIN_VERTICAL_PACKS:
            with self.subTest(pack["slug"]):
                self.assertTrue(pack.get("knowledge_entries"))
                self.assertTrue((pack.get("demo_task") or {}).get("prompt"))


class TheNewIndustryTemplatesTests(unittest.TestCase):
    NEW = ("bookkeeper", "payroll-clerk", "quote-clerk", "dispatcher")

    def test_they_exist(self):
        for name in self.NEW:
            with self.subTest(name):
                self.assertIn(name, TEMPLATE_NAMES)

    def test_they_carry_their_own_knowledge(self):
        by_name = {t["name"]: t for t in BUILTIN_TEMPLATES}
        for name in self.NEW:
            with self.subTest(name):
                self.assertGreater(len(by_name[name].get("knowledge_template", "")), 400)

    def test_the_money_roles_say_they_do_not_decide(self):
        """Ein Agent, der eine Buchung als geprueft ausgibt, richtet mehr Schaden
        an als einer, der gar nichts tut."""
        by_name = {t["name"]: t for t in BUILTIN_TEMPLATES}
        for name in ("bookkeeper", "payroll-clerk"):
            with self.subTest(name):
                self.assertIn("HAFTUNGSHINWEIS", by_name[name]["knowledge_template"])

    def test_the_quote_role_forbids_guessing_prices(self):
        """Geschaetzte Preise kosten Marge — und zwar unbemerkt."""
        by_name = {t["name"]: t for t in BUILTIN_TEMPLATES}
        text = by_name["quote-clerk"]["knowledge_template"].lower()
        self.assertIn("preisliste", text)
        self.assertIn("nie geschätzt", text)


class TheTaxPackWorksWithoutDatevTests(unittest.TestCase):
    """#393 ist pausiert. Das Paket darf nicht davon abhaengen."""

    def test_it_does_not_promise_datev(self):
        pack = get_pack("steuerkanzlei")
        blob = (pack["description"] + str(pack["knowledge_entries"])
                + str(pack["demo_task"])).lower()
        self.assertNotIn("datev", blob)

    def test_the_demo_task_stands_on_its_own(self):
        pack = get_pack("steuerkanzlei")
        self.assertIn("§14", pack["demo_task"]["prompt"])


if __name__ == "__main__":
    unittest.main()
