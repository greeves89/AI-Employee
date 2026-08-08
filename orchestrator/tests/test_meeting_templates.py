"""Ablauf-Vorlagen fuer Besprechungen (#14).

Ohne Ablauf wird eine Besprechung ein Rundgespraech: alle sagen etwas zum Thema, am
Ende steht eine Zusammenfassung, aber niemand hat entschieden. Die Stufen-Konfiguration
konnte das laengst steuern — sie musste nur jedes Mal von Hand zusammengestellt werden,
und deshalb tat es niemand.
"""

import unittest
from pathlib import Path

from app.core import meeting_templates as mt

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"


class CatalogTests(unittest.TestCase):
    def test_the_four_real_world_formats_exist(self):
        ids = {t["id"] for t in mt.list_templates()}
        self.assertEqual(ids, {"daily", "retro", "workshop", "entscheidung"})

    def test_every_template_has_stages(self):
        for tpl in mt.list_templates():
            with self.subTest(tpl=tpl["id"]):
                self.assertTrue(tpl["stages"])
                self.assertEqual(tpl["max_rounds"], tpl["total_rounds"])

    def test_stages_stay_short(self):
        """Eine Vorlage mit acht Stufen liest kein Mensch nach, und jede Stufe kostet
        eine volle Runde durch alle Teilnehmer."""
        for tpl in mt.list_templates():
            with self.subTest(tpl=tpl["id"]):
                self.assertLessEqual(len(tpl["stages"]), 4)

    def test_only_known_focus_values(self):
        allowed = {mt.FOCUS_RESEARCH, mt.FOCUS_DISCUSS, mt.FOCUS_DECIDE, mt.FOCUS_PLAN}
        for tpl in mt.list_templates():
            for stage in tpl["stages"]:
                with self.subTest(tpl=tpl["id"], stage=stage["name"]):
                    self.assertIn(stage["focus"], allowed)

    def test_daily_is_the_shortest(self):
        by_id = {t["id"]: t for t in mt.list_templates()}
        self.assertEqual(by_id["daily"]["total_rounds"],
                         min(t["total_rounds"] for t in mt.list_templates()))

    def test_decision_formats_end_in_a_decision(self):
        """Sonst ist es wieder nur ein Gespraech."""
        by_id = {t["id"]: t for t in mt.list_templates()}
        for key in ("retro", "entscheidung"):
            with self.subTest(tpl=key):
                self.assertEqual(by_id[key]["stages"][-1]["focus"], mt.FOCUS_DECIDE)

    def test_only_the_workshop_builds_an_artifact(self):
        """Ein Workshop ohne Ergebnis ist ein laengeres Gespraech — ein Daily mit
        Artefakt waere dagegen unsinnig teuer."""
        by_id = {t["id"]: t for t in mt.list_templates()}
        self.assertTrue(by_id["workshop"]["deliverable"])
        self.assertFalse(by_id["daily"]["deliverable"])

    def test_retro_needs_a_moderator(self):
        """Ohne Moderator wiederholt eine Retro die Beschwerden, statt sie zu buendeln."""
        by_id = {t["id"]: t for t in mt.list_templates()}
        self.assertTrue(by_id["retro"]["use_moderator"])


class ApplyTests(unittest.TestCase):
    def test_unknown_template_is_none(self):
        self.assertIsNone(mt.apply_template("gibtsnicht"))
        self.assertIsNone(mt.apply_template(""))

    def test_case_and_spacing_are_forgiving(self):
        self.assertIsNotNone(mt.apply_template("  Daily "))

    def test_overrides_win(self):
        """Die Vorlage ist ein Startpunkt, keine Zwangsjacke."""
        out = mt.apply_template("daily", {"use_moderator": True})
        self.assertTrue(out["use_moderator"])

    def test_none_overrides_are_ignored(self):
        out = mt.apply_template("retro", {"use_moderator": None})
        self.assertTrue(out["use_moderator"])

    def test_stages_are_copies(self):
        """Sonst veraendert ein Aufrufer die Vorlage fuer alle folgenden."""
        first = mt.apply_template("daily")
        first["stages_config"][0]["rounds"] = 99
        self.assertEqual(mt.apply_template("daily")["stages_config"][0]["rounds"], 1)


class WiringTests(unittest.TestCase):
    SRC = ORCH / "app/api/meeting_rooms.py"

    def test_endpoint_exists(self):
        self.assertIn('@router.get("/templates")', self.SRC.read_text())

    def test_route_order_is_safe(self):
        """/templates MUSS vor /{room_id} stehen, sonst wird 'templates' als
        Raum-ID gelesen — sichtbar nur als 404 auf einen toten Knopf."""
        from app.api import meeting_rooms

        paths = [r.path for r in meeting_rooms.router.routes]
        generic = min(i for i, p in enumerate(paths) if "{room_id}" in p)
        self.assertLess(paths.index("/meeting-rooms/templates"), generic)

    def test_create_accepts_a_template(self):
        src = self.SRC.read_text()
        self.assertIn("template: str | None", src)
        self.assertIn("apply_template", src)

    def test_explicit_stages_beat_the_template(self):
        src = self.SRC.read_text()
        self.assertIn("Eigene Stufen schlagen die Vorlage", src)

    def test_unknown_template_is_rejected_loudly(self):
        """Still auf Standardwerte zurueckzufallen waere schlimmer: der Nutzer
        glaubt, er habe eine Retro angelegt."""
        src = self.SRC.read_text()
        self.assertIn("Unbekannte Vorlage", src)


if __name__ == "__main__":
    unittest.main()
