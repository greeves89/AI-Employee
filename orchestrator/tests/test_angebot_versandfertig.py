"""Angebote mit echten Firmendaten, als sauberes PDF (#893).

Befund aus dem Markttest: Das Angebot der Vorlage „Angebot & Kalkulation“ hatte
einen erfundenen Absender und Kunden (der Skill gab „Kunde: Muster GmbH“ vor),
interne Hinweise standen im Kundendokument, und das PDF trug Browser-Kopf und
-Fuß. Firmendaten wurden nie erfragt.

Geprüft wird hier, was der Agent bekommt — und mit dem Golden-Test, was er tut:
ohne Firmenstammdaten muss er nachfragen, statt „Muster GmbH“ einzusetzen.
"""

import unittest
from pathlib import Path

from app.core import eval_harness, eval_seeds, mitgelieferte_skills
from app.core.agent_templates import BUILTIN_TEMPLATES

ORCH = Path(__file__).resolve().parents[1]


def _golden(item_id: str) -> dict:
    for satz in eval_seeds.BUILTIN_EVAL_SETS:
        for item in satz["items"]:
            if item["id"] == item_id:
                return item
    raise AssertionError(f"Golden-Test {item_id} fehlt")


class GoldenAngebotOhneFirmendatenTests(unittest.TestCase):
    def setUp(self):
        self.item = _golden("angebot-ohne-firmendaten")

    def test_erfundener_absender_faellt_durch(self):
        antwort = (
            "Hier ist Ihr Angebot: Malerbetrieb Muster GmbH, Musterstraße 1. "
            "40 m² Wandfläche, zwei Anstriche, 520,00 € netto. PDF liegt bereit."
        )
        self.assertFalse(eval_harness.check_item(self.item, antwort)["ok"])

    def test_rueckfrage_nach_firmenstammdaten_besteht(self):
        antwort = (
            "Bevor ich das Angebot erstelle, brauche ich einmalig Ihre Firmenstammdaten: "
            "Firma mit Rechtsform, Anschrift, Registergericht und -nummer, Geschäftsführer, "
            "Steuernummer und Bankverbindung. Außerdem: An welchen Kunden geht das Angebot?"
        )
        self.assertTrue(eval_harness.check_item(self.item, antwort)["ok"])

    def test_leere_antwort_besteht_nicht(self):
        self.assertFalse(eval_harness.check_item(self.item, "")["ok"])


class SkillUndVorlageTests(unittest.TestCase):
    def setUp(self):
        self.skill = next(s for s in mitgelieferte_skills.laden() if s["name"] == "angebot-kalkulieren")
        self.vorlage = next(t for t in BUILTIN_TEMPLATES if t["name"] == "quote-clerk")

    def test_skill_gibt_keinen_platzhalter_als_kunden_vor(self):
        self.assertNotIn("Kunde: Muster GmbH", self.skill["content"])

    def test_skill_holt_firmenstammdaten_und_trennt_kunde_von_intern(self):
        inhalt = self.skill["content"]
        self.assertIn("Firmenstammdaten", inhalt)
        self.assertIn("Kundenangebot", inhalt)
        self.assertIn("Interne Kalkulation", inhalt)
        self.assertIn("dokument pdf", inhalt)

    def test_vorlage_kennt_firmenstammdaten_und_dokument_pdf(self):
        wissen = self.vorlage["knowledge_template"]
        self.assertIn("Firmenstammdaten", wissen)
        self.assertIn("dokument pdf", wissen)


class AnleitungTests(unittest.TestCase):
    def test_gemeinsame_anleitung_aller_laufzeiten_nennt_dokument_pdf(self):
        quelle = (ORCH / "app/core/agent_manager.py").read_text(encoding="utf-8")
        start = quelle.index('DEFAULT_CLAUDE_MD = """') + len('DEFAULT_CLAUDE_MD = """')
        ende = quelle.index('"""', start)
        self.assertIn("dokument pdf", quelle[start:ende])


if __name__ == "__main__":
    unittest.main()
