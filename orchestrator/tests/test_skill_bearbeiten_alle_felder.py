"""Beim Bearbeiten eines Skills durch einen Menschen kommen ALLE Felder an.

Die Agenten-Fassung des Eingabemodells hiess ebenfalls ``SkillUpdate`` und ueberschrieb
die Fassung fuer Menschen: ``PUT /skills/marketplace/{id}`` verwarf still Name,
Kategorie, Status, Pfade, Rollen und „öffentlich“ (gefunden 03.10.2026).
"""

import unittest

from app.api import skill_marketplace as sm


class EingabemodelleTests(unittest.TestCase):
    def test_menschen_route_nimmt_alle_felder(self):
        felder = set(sm.update_skill.__annotations__["body"].model_fields)
        for feld in ("name", "description", "content", "category", "status", "paths", "roles", "is_public"):
            self.assertIn(feld, felder)

    def test_agenten_route_bleibt_eingeschraenkt(self):
        felder = set(sm.agent_update_skill.__annotations__["body"].model_fields)
        self.assertEqual(felder, {"description", "content", "feedback"})

    def test_kein_name_doppelt(self):
        self.assertIsNot(sm.update_skill.__annotations__["body"], sm.agent_update_skill.__annotations__["body"])


if __name__ == "__main__":
    unittest.main()
