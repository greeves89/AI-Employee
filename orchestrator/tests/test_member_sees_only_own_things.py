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
MANAGER = (ROOT / "orchestrator/app/core/agent_manager.py").read_text()


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
        self.assertIn("...(isAdmin ? [", VIEW)
        block = VIEW.split("...(isAdmin ? [", 1)[1][:300]
        self.assertIn('id: "voice"', block)
        self.assertIn('id: "system"', block)

    def test_the_user_owned_tab_stays_visible(self):
        """„Meine KI-Zugaenge" gehoert JEDEM — sonst kann niemand sein eigenes
        Abo verbinden."""
        vor = VIEW.split("...(isAdmin ? [", 1)[0]
        self.assertIn('id: "meine"', vor)

    def test_a_direct_link_does_not_land_on_an_empty_page(self):
        """Alte Verknuepfung oder Adresszeile — sonst sieht der Nutzer genau die
        leere Seite wieder, die wir abgeschafft haben."""
        self.assertIn('secTab === "voice" || secTab === "system"', VIEW)
        self.assertIn('setSecTab("modelle")', VIEW)


if __name__ == "__main__":
    unittest.main()
