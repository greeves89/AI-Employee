"""Ein normaler Nutzer sieht nur, was ihm gehoert — Agenten wie Reiter.

Zwei Meldungen desselben Tages, gleiche Wurzel:

1. „Habe mir einen Test User angelegt… wieso sehe ich den Testi CustomLLM?"
   Ein Agent OHNE Besitzer war fuer jeden sichtbar. Im Code stand das sogar als
   Absicht („+ unowned + shared") — nur wird ein Agent nicht durch eine
   Entscheidung besitzlos, sondern durch ein Versehen: ein Skript ohne
   ``user_id``, ein geloeschter Nutzer, eine Migration. Genau so ist er
   entstanden. Ein Versehen darf keine Freigabe ausloesen, zumal es fuers Teilen
   einen ausdruecklichen Weg gibt (``AgentAccess``, ``shared_for_rooms``).

2. „Als Member normaler User wieso sehe ich die beiden Dinge?" — die Reiter
   „Voice" und „System" in den Einstellungen. Ihre Inhalte waren laengst
   adminbeschraenkt, die Reiter selbst nicht: der Nutzer klickte und sah eine
   leere Seite. Ein Reiter ohne Inhalt ist schlechter als kein Reiter, weil er
   wie ein Fehler aussieht.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = (ROOT / "orchestrator/app/api/agents.py").read_text()
VIEW = (ROOT / "frontend/src/app/settings/view.tsx").read_text()
# Seit #899: /settings zeigt nur noch „Meine Einstellungen“; die anlagenweiten
# Bereiche (frueher Reiter dort) stehen einzeln in der Admin-Konsole.
MEINE = (ROOT / "frontend/src/app/settings/meine-einstellungen.tsx").read_text()
SETTINGS_SEITE = (ROOT / "frontend/src/app/settings/page.tsx").read_text()
KONSOLE = (ROOT / "frontend/src/app/admin/page.tsx").read_text()
MANAGER = (ROOT / "orchestrator/app/core/agent_manager.py").read_text()

# 2026-08-27: the SAME "unowned = shared" mistake this file was written to guard
# against had quietly drifted back in — not in agents.py, but in five OTHER
# endpoints that never got the memo (found on a multi-department customer
# install, where it leaked one department's tasks/costs/notifications
# to another). Their code comments even cited agents.py as the precedent
# ("dieselbe Regel wie in der Agentenliste") — a precedent that had already
# stopped being true. Guarding all five here, same file, same reasoning,
# instead of five new near-duplicate test files.
_UNOWNED_LEAK_FILES = {
    "tasks.py": ROOT / "orchestrator/app/api/tasks.py",
    "ws.py": ROOT / "orchestrator/app/api/ws.py",
    "evals.py": ROOT / "orchestrator/app/api/evals.py",
    "schedules.py": ROOT / "orchestrator/app/api/schedules.py",
    "notifications.py": ROOT / "orchestrator/app/api/notifications.py",
}


class OwnerlessLeakDoesNotReturnTests(unittest.TestCase):
    def test_no_endpoint_treats_unowned_as_shared(self):
        offenders = []
        for label, path in _UNOWNED_LEAK_FILES.items():
            code = [
                line for line in path.read_text().splitlines()
                if not line.lstrip().startswith("#")
            ]
            if any("user_id.is_(None)" in line or "user_id == None" in line for line in code):
                offenders.append(label)
        self.assertEqual(offenders, [])


class UnownedIsNotSharedTests(unittest.TestCase):
    def test_the_personal_list_no_longer_lets_unowned_through(self):
        self.assertNotIn("if a.user_id is None or a.user_id == user.id", API)

    def test_ownership_is_still_what_counts(self):
        self.assertIn("if a.user_id == user.id or a.id in accessible_ids", API)

    def test_both_places_were_fixed(self):
        """Die Regel stand zweimal im Code — eine Haelfte zu reparieren haette
        den Fehler nur verschoben.

        Geprueft werden nur echte Code-Zeilen: der erklaerende Kommentar nennt
        die alte Bedingung absichtlich weiter."""
        code = [z for z in API.splitlines() if not z.lstrip().startswith("#")]
        treffer = [z for z in code if "a.user_id is None" in z]
        self.assertEqual(treffer, [])

    def test_explicit_sharing_still_works(self):
        """Besprechungsraeume teilen bewusst — das darf nicht mit wegfallen."""
        self.assertIn('room_pool and getattr(a, "shared_for_rooms", False)', API)

    def test_creating_without_an_owner_is_logged(self):
        """Es bleibt moeglich (interne Wege brauchen es), aber nicht unbemerkt:
        so ein Agent taucht jetzt in KEINER persoenlichen Liste mehr auf."""
        self.assertIn("OHNE Besitzer", MANAGER)


class EmptyTabsAreHiddenTests(unittest.TestCase):
    def test_voice_and_system_are_admin_only(self):
        """Sprache und System gehoeren der Anlage: sie stehen nur noch in der
        Admin-Konsole, nicht mehr auf der Seite, die jeder Nutzer oeffnet."""
        self.assertIn("MeineEinstellungenView", SETTINGS_SEITE)
        for nur_admin in ("<VoiceSettings", "<SystemControl", "SettingsView", "<LizenzBereich"):
            with self.subTest(nur_admin):
                self.assertNotIn(nur_admin, MEINE)
        self.assertIn('{tab === "sprache" && <VoiceSettings />}', KONSOLE)

    def test_the_user_owned_tab_stays_visible(self):
        """„Meine KI-Zugaenge" gehoert JEDEM — sonst kann niemand sein eigenes
        Abo verbinden."""
        self.assertIn("<MyAiCredentials />", MEINE)

    def test_a_direct_link_does_not_land_on_an_empty_page(self):
        """Alte Verknuepfung oder Adresszeile — das Verhalten der Weiterleitung
        prueft test_admin_navigation_weiterleitung; hier nur, dass die Seite sie
        auch benutzt."""
        self.assertIn("alteEinstellungenWeiterleitung(", MEINE)


if __name__ == "__main__":
    unittest.main()


class TheModelsTabHasItsOwnMemberViewTests(unittest.TestCase):
    """„Wieso sollte ein normaler User das hier alles sehen?" — Provider-
    Konfiguration, Plattform-Login bei ChatGPT, Max Turns, Anzahl gleichzeitiger
    Agenten. Nichts davon kann ein Member einstellen; er sah eine
    Bedienoberflaeche, die auf keinen seiner Klicks reagiert.

    Seine Frage ist eine andere: WELCHE Modelle stehen mir zur Verfuegung. Genau
    das zeigt die Member-Sicht — lesend, ohne einen einzigen Schalter.
    """

    KOMPONENTE = (ROOT / "frontend/src/components/settings/available-models.tsx").read_text()

    def test_members_get_a_different_view(self):
        self.assertIn("<AvailableModels />", MEINE)
        self.assertNotIn("ModelCatalogAdmin", MEINE)

    def test_admins_keep_the_configuration(self):
        self.assertIn('bereich === "modelle"', VIEW)
        self.assertIn("<ModelCatalogAdmin />", VIEW)

    def test_the_member_view_has_no_controls(self):
        """Kein Speichern, kein Umschalten — sonst ist es wieder eine
        Bedienoberflaeche ohne Wirkung."""
        for verboten in ("onClick", "<button", "<input", "<select"):
            with self.subTest(element=verboten):
                self.assertNotIn(verboten, self.KOMPONENTE)

    def test_it_relies_on_the_server_filter(self):
        """``/ai-accounts`` liefert einem Nicht-Administrator ohnehin nur
        Freigegebenes (default-deny). Die Anzeige ist keine Sicherheitsgrenze —
        das muss im Code stehen, sonst verlaesst sich spaeter jemand darauf."""
        self.assertIn("keine Sicherheitsgrenze", self.KOMPONENTE)

    def test_the_empty_case_says_what_to_do(self):
        """Der haeufigste Fall bei einem neuen Nutzer: nichts freigegeben."""
        self.assertIn("Noch kein Modell freigegeben", self.KOMPONENTE)
        self.assertIn("Meine KI-Zugänge", self.KOMPONENTE)

    def test_the_save_button_is_gone_where_nothing_is_saved(self):
        """„Meine KI-Zugaenge" sichert sofort beim Verbinden. Ein Knopf, der
        nichts tut, laesst den Nutzer glauben, er haette etwas vergessen."""
        self.assertNotIn("handleSave", MEINE)
        self.assertNotIn("Einstellungen speichern", MEINE)
