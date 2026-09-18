"""Issue #787, letzter Punkt: die Agent-Settings-Seite war ein 1665-Zeilen-
Einzel-Scroll ohne Gruppierung (Kosten-Stats, Ressourcenlimits, Secrets,
Webhooks etc. alle ungruppiert hintereinander). Jetzt in fuenf aufklappbare
Abschnitte gebuendelt; Autonomie-Matrix, Sudo-Pakete und Computer-Use-
Standard sitzen zusammen in "Zugriff & Rechte" (vorher war die Matrix weit
oben, die anderen beiden weit unten -- getrennt trotz derselben Sache).

Reiner Quelltext-Scan wie bei den uebrigen Frontend-Vertraegen in diesem
Baum -- kein JS-Test-Runner vorhanden.
"""
import re
import unittest
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[2]
        / "frontend/src/app/agents/[id]/page.tsx")


class AccordionStructureTests(unittest.TestCase):
    def setUp(self):
        self.src = PAGE.read_text()

    def test_five_accordion_groups_exist(self):
        for title in (
            "Aussehen & Verhalten",
            "Modell & Verhalten",
            "Verbindungen",
            "Zugriff & Rechte",
            "Ressourcen & Limits",
        ):
            with self.subTest(title=title):
                self.assertIn(f'title="{title}"', self.src)

    def test_opens_and_closes_balance(self):
        opens = len(re.findall(r"<SettingsAccordionSection\b", self.src))
        closes = self.src.count("</SettingsAccordionSection>")
        self.assertEqual(opens, closes)
        self.assertEqual(opens, 5)

    def test_connections_group_carries_the_secrets_warning(self):
        """Telegram-Token, Webhook-Bearer-Token, MCP-Bearer-Token -- diese
        Gruppe traegt echte Secrets und muss zu und markiert starten. Prueft
        die tatsaechliche Eroeffnungszeile, kein Zeichenfenster (#726) --
        title und secretsWarning muessen auf DERSELBEN Zeile stehen."""
        zeilen = [z for z in self.src.splitlines() if 'title="Verbindungen"' in z]
        self.assertEqual(len(zeilen), 1, "Genau eine Eroeffnungszeile erwartet")
        self.assertIn("secretsWarning", zeilen[0])

    def test_access_group_bundles_matrix_permissions_and_computer_use(self):
        """Der eigentliche Sinn des Umbaus: die drei Zugriffs-Konzepte, die
        vorher an drei verschiedenen Stellen der Seite lagen, sitzen jetzt
        nebeneinander."""
        start = self.src.index('title="Zugriff & Rechte"')
        end = self.src.index("</SettingsAccordionSection>", start)
        block = self.src[start:end]
        self.assertIn("AutonomyMatrix", block)
        self.assertIn("PermissionPackagesPanel", block)
        self.assertIn("ComputerUseDefaultPanel", block)

    def test_status_messages_stay_outside_any_accordion(self):
        """Erfolg/Fehler-Meldungen muessen immer sichtbar sein, auch wenn
        alle Abschnitte zu sind -- duerfen also in keiner Gruppe stecken."""
        start = self.src.index("{/* Status messages */}")
        # Der naechste Gruppen-Rahmen NACH den Status-Meldungen darf nicht
        # unmittelbar folgen, ohne dass die letzte Gruppe (Ressourcen &
        # Limits) vorher sauber geschlossen wurde.
        before = self.src[:start]
        self.assertEqual(
            before.count("<SettingsAccordionSection"),
            before.count("</SettingsAccordionSection>"),
            "Eine Gruppe ist an dieser Stelle noch offen -- Status-Meldungen "
            "wuerden innerhalb eines Akkordeons stecken.",
        )


if __name__ == "__main__":
    unittest.main()
