"""Model-Router und Entwicklungs-Tendenz — beide entschieden auf duenner Luft.

Nutzerfrage vom 19.08.2026: „funktioniert eigentlich der model router?"

Nachgesehen auf der Anlage: er war bei GENAU EINEM Agenten eingeschaltet, und
seine Regeln waren drei leere Strings. Ursache war die Oberflaeche — sie zeigte
die Vorgaben als PLATZHALTER, der Zustand startete leer, gespeichert wurde "".

``route_model`` gab die leeren Regeln brav zurueck (``dict.get``), obwohl der
eigene Docstring „or None if the resolved tier has no rule configured"
verspricht. Ergebnis: **146 Auftraege mit leerem Modellnamen** in sieben Tagen.
Aufgefallen ist es nie, weil der Agent am Ende auf seine Vorgabe zurueckfiel
(``model = model or settings.default_model``) — der Router war eingeschaltet und
wirkungslos.

Dieselbe Sorte Fehler in der Entwicklungs-Karte: „Tendenz: schlechter" stand auf
212 jungen gegen VIER alte Aufgaben.
"""

import unittest

from app.core.model_router import DEFAULT_ROUTER_RULES, route_model


class EmptyRulesAreNotADecisionTests(unittest.TestCase):
    LEER = {"simple": "", "standard": "", "complex": ""}

    def test_an_empty_rule_falls_back_to_the_default(self):
        """Vorher kam hier '' heraus — ein Modellname, den es nicht gibt."""
        self.assertEqual(
            route_model("Was ist 2+2?", self.LEER),
            DEFAULT_ROUTER_RULES["simple"],
        )

    def test_no_tier_ever_resolves_to_an_empty_string(self):
        for prompt in ("Was ist 2+2?", "Fasse das zusammen",
                       "Baue eine Webanwendung mit Authentifizierung und Tests"):
            with self.subTest(prompt=prompt[:30]):
                self.assertNotEqual(route_model(prompt, self.LEER), "")
                self.assertTrue((route_model(prompt, self.LEER) or "").strip())

    def test_whitespace_counts_as_empty(self):
        self.assertEqual(
            route_model("Was ist 2+2?", {"simple": "   "}),
            DEFAULT_ROUTER_RULES["simple"],
        )

    def test_a_real_rule_still_wins(self):
        self.assertEqual(
            route_model("Was ist 2+2?", {"simple": "mein-modell"}), "mein-modell"
        )

    def test_no_rules_at_all_uses_the_defaults(self):
        self.assertEqual(route_model("Was ist 2+2?"), DEFAULT_ROUTER_RULES["simple"])


class TheUiStoresRealValuesTests(unittest.TestCase):
    """Ein Platzhalter sieht aus wie ein Wert und ist keiner — genau daran ist
    es gescheitert. Eine Auswahlliste kann diesen Fehler nicht machen."""

    from pathlib import Path
    SEITE = (Path(__file__).resolve().parents[2]
             / "frontend/src/app/agents/[id]/page.tsx").read_text()

    def test_the_tiers_are_dropdowns_now(self):
        block = self.SEITE.split('(["simple", "standard", "complex"] as const)', 1)
        self.assertEqual(len(block), 2, "Router-Block nicht gefunden")
        self.assertIn("<select", block[1][:2500])

    def test_the_defaults_are_real_values_not_placeholders(self):
        self.assertIn("ROUTER_VORGABEN", self.SEITE)
        block = self.SEITE.split("const [routerRules, setRouterRules]", 1)[1][:400]
        self.assertIn("|| ROUTER_VORGABEN.simple", block)

    def test_only_released_models_are_offered(self):
        """Dieselbe Quelle wie beim Anlegen eines Agenten — sonst waere hier
        waehlbar, was ein Administrator gesperrt hat."""
        self.assertIn("api.getModelCatalog()", self.SEITE)

    def test_a_stored_but_blocked_model_stays_visible(self):
        """Sonst springt die Auswahl beim Oeffnen stumm auf ein anderes Modell
        und der Nutzer merkt die Aenderung nie."""
        self.assertIn("(nicht freigegeben)", self.SEITE)

    def test_choosing_saves_immediately(self):
        """Eine Auswahlliste hat kein sinnvolles „Feld verlassen" — und ein
        stillschweigend nicht gespeicherter Wert war hier schon das Problem."""
        block = self.SEITE.split("<select", 1)[1][:900]
        self.assertIn("saveModelRouter(", block)


class TheTrendNeedsDataOnBothSidesTests(unittest.TestCase):
    from pathlib import Path
    QUELLE = (Path(__file__).resolve().parents[1] / "app/api/analytics.py").read_text()

    def test_each_half_needs_a_minimum(self):
        self.assertIn("MINDESTENS_JE_HAELFTE", self.QUELLE)
        block = self.QUELLE.split("MINDESTENS_JE_HAELFTE = ", 1)[1][:300]
        self.assertIn("recent_total >= MINDESTENS_JE_HAELFTE", block)
        self.assertIn("older_total >= MINDESTENS_JE_HAELFTE", block)

    def test_too_little_data_says_so_instead_of_judging(self):
        self.assertIn('trend = "zu wenig Daten"', self.QUELLE)


if __name__ == "__main__":
    unittest.main()
