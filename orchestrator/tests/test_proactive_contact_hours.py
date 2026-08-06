"""Arbeitszeit des Ansprechpartners (HANDOVER.md Schritt 1): ohne dieses Feld kann
der Agent nicht wissen, ob er stoeren darf. Deckt die "Vierfach-Luecke" ab, die im
Projekt schon dreimal zuschlug (Stimme, Verzeichnis-ID, Berechtigungen): eine
Einstellung existiert, aber der Weg zu ihr fehlt an einer der vier Stellen
(Erlaubnisliste/Schema, Validierung, PATCH-Mapping, Rueckgabe an die Oberflaeche).
"""

import unittest

from fastapi import HTTPException

from app.api.agents import ProactiveUpdate, _validated_contact_hours
from app.services.scheduler_service import _contact_hours_note


class ValidatedContactHoursTests(unittest.TestCase):
    def test_unset_returns_empty(self):
        self.assertEqual(_validated_contact_hours("", "", ""), {})

    def test_full_valid_window_is_normalized(self):
        got = _validated_contact_hours("09:00", "18:00", "Europe/Berlin")
        self.assertEqual(got, {"start": "09:00", "end": "18:00", "timezone": "Europe/Berlin"})

    def test_missing_timezone_defaults_to_utc(self):
        got = _validated_contact_hours("09:00", "18:00", "")
        self.assertEqual(got["timezone"], "UTC")

    def test_one_sided_window_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            _validated_contact_hours("09:00", "", "")
        self.assertEqual(ctx.exception.status_code, 422)

    def test_malformed_time_is_rejected(self):
        for bad in ("9:00", "09:60", "25:00", "abc"):
            with self.assertRaises(HTTPException):
                _validated_contact_hours(bad, "18:00", "")

    def test_unknown_timezone_is_rejected(self):
        with self.assertRaises(HTTPException):
            _validated_contact_hours("09:00", "18:00", "Not/A_Zone")

    def test_end_before_start_is_still_accepted(self):
        """Kein Anspruch, dass das Fenster ueber Mitternacht sinnvoll ist zu
        validieren hier — nur Format/Zeitzone. Reihenfolge ist Sache des Agenten."""
        got = _validated_contact_hours("22:00", "06:00", "UTC")
        self.assertEqual(got["start"], "22:00")


class ContactHoursPromptNoteTests(unittest.TestCase):
    def test_no_config_yields_no_note(self):
        self.assertEqual(_contact_hours_note({}), "")
        self.assertEqual(_contact_hours_note({"contact_hours": {}}), "")

    def test_configured_hours_render_a_note(self):
        note = _contact_hours_note({
            "contact_hours": {"start": "09:00", "end": "18:00", "timezone": "Europe/Berlin"}
        })
        self.assertIn("09:00", note)
        self.assertIn("18:00", note)
        self.assertIn("Europe/Berlin", note)

    def test_partial_hours_yield_no_note(self):
        self.assertEqual(_contact_hours_note({"contact_hours": {"start": "09:00"}}), "")


class ProactiveUpdateSchemaTests(unittest.TestCase):
    """Erlaubnisliste/Schema-Stelle der Vierfach-Luecke: das Request-Schema muss
    die drei Felder tragen, sonst kommt beim Speichern nichts an."""

    def test_schema_carries_all_three_fields(self):
        for field in ("contact_hours_start", "contact_hours_end", "contact_timezone"):
            self.assertIn(field, ProactiveUpdate.model_fields)

    def test_fields_default_to_none_so_partial_saves_do_not_wipe_them(self):
        body = ProactiveUpdate(enabled=True, interval_seconds=3600)
        self.assertIsNone(body.contact_hours_start)
        self.assertIsNone(body.contact_hours_end)
        self.assertIsNone(body.contact_timezone)


class UpdateProactiveConfigWiringTests(unittest.TestCase):
    """PATCH-Mapping/Rueckgabe-Stellen: die Handler-Quelle muss die validierte
    Funktion tatsaechlich aufrufen und ins proactive-dict schreiben — sonst
    meldet die Oberflaeche "Gespeichert" und nichts passiert."""

    def test_update_endpoint_calls_the_validator(self):
        import inspect
        from app.api.agents import update_proactive_config
        src = inspect.getsource(update_proactive_config)
        self.assertIn("_validated_contact_hours", src)
        self.assertIn('"contact_hours": new_hours', src)

    def test_get_endpoint_returns_the_whole_proactive_dict(self):
        """contact_hours lebt im proactive-dict — GET gibt proactive komplett
        zurueck, keine eigene Extraktion noetig, aber die Rueckgabe darf es
        nicht herausfiltern."""
        import inspect
        from app.api.agents import get_proactive_config
        src = inspect.getsource(get_proactive_config)
        self.assertIn('"proactive": proactive', src)


if __name__ == "__main__":
    unittest.main()
