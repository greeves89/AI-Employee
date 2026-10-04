"""Golden-Tests für die Fach-Vorlagen: DATEV brutto (#894) und „erst fragen“ (#893).

In der Abnahme von v1.362.1 fielen zwei Dinge auf, die ein Golden-Test fangen muss:

* Der DATEV-Buchungsstapel trug bei BU 9 den Nettobetrag (``100,00;S;…;9``).
* Der Angebots-Agent lieferte zuerst ein PDF mit „[Ihr Firmenname]“ aus und fragte
  danach — die Rückfrage allein bestand den alten Test trotzdem.

Dazu: Claude Code meldet MCP-Werkzeuge mit Server-Präfix. Ohne Kurzform griffen
Werkzeug-Erwartungen der Sammlungen bei Claude-Code-Agenten nie.
"""

import asyncio
import unittest
from types import SimpleNamespace

from app.core import eval_harness, eval_seeds


def _golden(item_id: str) -> dict:
    for satz in eval_seeds.BUILTIN_EVAL_SETS:
        for item in satz["items"]:
            if item["id"] == item_id:
                return item
    raise AssertionError(f"Golden-Test {item_id} fehlt")


class DatevBruttoTests(unittest.TestCase):
    def setUp(self):
        self.item = _golden("datev-brutto-bei-bu-schluessel")

    def test_nettobetrag_bei_bu_9_faellt_durch(self):
        antwort = "Umsatz;Soll/Haben;Konto;Gegenkonto;BU\n100,00;S;4930;1600;9;15.08.2026;2026-0815;Bürobedarf"
        self.assertFalse(eval_harness.check_item(self.item, antwort)["ok"])

    def test_bruttobetrag_mit_soll_haben_und_bu_9_besteht(self):
        antwort = (
            "Buchungsliste zum Import durch die Kanzlei:\n"
            "Umsatz;Soll/Haben;Konto;Gegenkonto;BU;Belegdatum;Belegfeld 1;Buchungstext\n"
            "119,00;H;1600;4930;9;15.08.2026;2026-0815;Bürobedarf Lieferant A"
        )
        self.assertTrue(eval_harness.check_item(self.item, antwort)["ok"])

    def test_brutto_ohne_soll_haben_kennzeichen_faellt_durch(self):
        antwort = "119,00;1600;4930;9;15.08.2026;2026-0815;Bürobedarf"
        self.assertFalse(eval_harness.check_item(self.item, antwort)["ok"])

    def test_sammlung_gibt_es_fuer_die_vorlage(self):
        ids = {s["id"] for s in eval_seeds.BUILTIN_EVAL_SETS}
        self.assertIn("builtin-buchhaltung", ids)


class AngebotErstFragenTests(unittest.TestCase):
    def setUp(self):
        self.item = _golden("angebot-ohne-firmendaten")
        self.frage = (
            "Bevor ich das Angebot erstelle, brauche ich einmalig Ihre Firmenstammdaten: "
            "Firma mit Rechtsform, Anschrift, Registergericht, Steuernummer und Bankverbindung."
        )

    def test_frage_ohne_ausgeliefertes_dokument_besteht(self):
        fakten = {"tools_called": ["brain_search", "memory_search"]}
        self.assertTrue(eval_harness.check_item(self.item, self.frage, fakten)["ok"])

    def test_erst_pdf_ausliefern_dann_fragen_faellt_durch(self):
        fakten = {"tools_called": ["Bash", "present_file"]}
        self.assertFalse(eval_harness.check_item(self.item, self.frage, fakten)["ok"])

    def test_platzhalter_in_eckigen_klammern_faellt_durch(self):
        antwort = "Hier Ihr Angebot von [Ihr Firmenname]. Bitte nennen Sie mir noch Ihre Firmendaten."
        self.assertFalse(eval_harness.check_item(self.item, antwort)["ok"])


class _Ergebnis:
    def __init__(self, werte):
        self._werte = werte

    def scalars(self):
        return self

    def all(self):
        return list(self._werte)


class _Db:
    def __init__(self, steps):
        self._steps = steps

    async def execute(self, _stmt):
        return _Ergebnis(self._steps)


class KurzformDerWerkzeugeTests(unittest.TestCase):
    def test_mcp_praefix_von_claude_code_zaehlt_als_werkzeug(self):
        from app.services.eval_service import gather_facts

        aufgabe = SimpleNamespace(id="t1", agent_id="a1", started_at=None)
        steps = [{"tool": "mcp__notifications__present_file"}, {"name": "bash"}]
        fakten = asyncio.run(gather_facts(_Db(steps), aufgabe))
        self.assertIn("present_file", fakten["tools_called"])
        self.assertIn("mcp__notifications__present_file", fakten["tools_called"])
        item = _golden("angebot-ohne-firmendaten")
        self.assertFalse(eval_harness.check_item(item, "Bitte nennen Sie Ihre Firmendaten.", fakten)["ok"])


if __name__ == "__main__":
    unittest.main()
