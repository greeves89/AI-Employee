"""Antworten braucht keine Freigabe.

Am 2026-08-12 bekam Mr. Design (L3) einen delegierten Auftrag mit ausdrücklicher
Ansage „Implementiere selbstständig" — und tat nichts. Auf die Frage, was ihn
gehindert habe, antwortete er selbst:

    „Nicht die Arbeit an Dateien/Shell selbst, sondern die Unsicherheit, ob
    bereits ein **normaler Chat-Reply** als ‚Chat senden' im Sinne der
    Approval-Regel zählt. Wenn ich das streng auslege, dürfte ich ohne Approval
    nicht einmal antworten."

Er wusste, dass er Dateien schreiben und Shell nutzen darf — beides steht bei
ihm unter ALLOWED. Er traute sich nur nicht, den **Rückweg zum Auftraggeber** zu
gehen. Die Matrix meint mit „Chat / Telegram senden" ausgehende Aktionen an
Dritte; für ein vorsichtiges Modell las sie sich wie ein Maulkorb.

Dazu kam der Auffangsatz „For anything not clearly covered, call
`request_approval`" — der galt für ALLES Unklare, also auch fürs Lesen, Denken
und Berichten.
"""

import unittest

import app.core.autonomy_matrix as am


class ReplyIsNeverGatedTests(unittest.TestCase):
    def setUp(self):
        self.prompt = am.matrix_to_prompt(am.matrix_for_level("l3"))

    def test_the_carve_out_is_present(self):
        self.assertIn("NEVER approval-gated", self.prompt,
                      "Ohne ausdrückliche Ausnahme liest ein vorsichtiges Modell "
                      "die eigene Antwort als freigabepflichtige Kommunikation")

    def test_it_names_all_three_return_paths(self):
        """Chat, Task-Ergebnis und Antwort an einen anderen Agenten — der
        Sub-Agent erreicht seinen Lead über alle drei."""
        for path in ("chat", "task result", "another agent"):
            with self.subTest(path):
                self.assertIn(path, self.prompt)

    def test_the_catch_all_is_limited_to_outside_effects(self):
        self.assertIn("effect outside your container", self.prompt)

    def test_allowed_capabilities_still_need_no_asking(self):
        self.assertIn("needs no asking", self.prompt)


class TheMatrixItselfIsUnchangedTests(unittest.TestCase):
    """Die Formulierung wird klarer — die Rechte bleiben, wie sie waren."""

    def test_l3_still_allows_files_and_shell(self):
        matrix = am.matrix_for_level("l3")
        for cap in ("file_read", "file_write", "shell_exec", "system_config", "web"):
            with self.subTest(cap):
                self.assertEqual(matrix[cap], am.ALLOW)

    def test_l3_still_asks_before_acting_on_the_outside_world(self):
        matrix = am.matrix_for_level("l3")
        for cap in ("email_m365", "external_api", "git_push", "purchases"):
            with self.subTest(cap):
                self.assertEqual(matrix[cap], am.ASK)


class MessagingIsFreeFromL2Tests(unittest.TestCase):
    """Dem eigenen Nutzer schreiben ist keine Handlung mit Aussenwirkung.

    Bis v1.178.3 war ``messaging`` bis einschliesslich L3 freigabepflichtig. Das
    hat Agenten davon abgehalten, ueberhaupt zu antworten — und damit ganze
    Auftraege blockiert. E-Mail, externe APIs, git push und Käufe bleiben davon
    unberuehrt: die brauchen weiterhin bis L4 eine Freigabe.
    """

    def test_l1_still_asks(self):
        """Die Nur-Lesen-Stufe bleibt die Nur-Lesen-Stufe."""
        self.assertEqual(am.matrix_for_level("l1")["messaging"], am.ASK)

    def test_l2_and_l3_may_write_to_their_user(self):
        for level in ("l2", "l3"):
            with self.subTest(level):
                self.assertEqual(am.matrix_for_level(level)["messaging"], am.ALLOW)

    def test_the_outside_world_stays_gated_at_l2_and_l3(self):
        """Die Lockerung gilt genau fuer EINE Fähigkeit, nicht fuer die Gruppe."""
        for level in ("l2", "l3"):
            for cap in ("email_m365", "external_api", "git_push", "purchases"):
                with self.subTest(level=level, cap=cap):
                    self.assertEqual(am.matrix_for_level(level)[cap], am.ASK)

    def test_full_autonomy_needs_no_carve_out(self):
        """Wo ohnehin alles erlaubt ist, gibt es nichts zu entschärfen."""
        full = am.matrix_to_prompt(am.matrix_for_level("l4"))
        self.assertIn("FULLY AUTONOMOUS", full)


if __name__ == "__main__":
    unittest.main()
