"""Der Sprach-Agent darf vor langsamen Aktionen nicht stumm werden.

Kundenwunsch 2026-08-04: „Ich brauche mehr 'Einen Moment, ich schau mal rein' /
'Moment, ich oeffne die App' — halt VOR der Nutzung eines Tools erstmal sprechen."

Der Prompt verbot das vorher ausdruecklich ("kein Beschreiben, welches Tool du
gleich nutzt"), weil er gegen Laut-Denken geschrieben war. Beides muss
nebeneinander gelten: Gedankengang nein, Wartehinweis ja.
"""

import unittest

from app.services.realtime_voice_session import _system_prompt


class SpeakBeforeToolsTests(unittest.TestCase):
    def setUp(self):
        self.p = _system_prompt("TestBot", "Rolle", "de")

    def test_announcing_before_slow_actions_is_required(self):
        self.assertIn("NIE STUMM ARBEITEN", self.p)

    def test_examples_are_given(self):
        """Ohne Beispiele erfindet das Modell steife Formulierungen."""
        self.assertIn("Einen Moment", self.p)
        self.assertIn("Bildschirm", self.p)

    def test_thinking_aloud_stays_forbidden(self):
        """Die urspruengliche Regel darf nicht verlorengehen."""
        self.assertIn("NICHT LAUT DENKEN", self.p)
        self.assertIn("Denke still", self.p)

    def test_what_versus_why_is_spelled_out(self):
        """Die Abgrenzung ist der ganze Punkt — ohne sie kippt es in Laut-Denken."""
        self.assertIn("ansagen WAS gleich passiert", self.p)

    def test_no_canned_repetition(self):
        self.assertIn("Variiere die Formulierung", self.p)

    def test_rule_reaches_both_engines(self):
        """AWS (Nova Sonic) und Azure (gpt-realtime) bekommen denselben Prompt —
        SKBS laeuft auf Azure, der Pi auf AWS."""
        import inspect
        from app.services.realtime_voice_session import RealtimeVoiceSession
        src = inspect.getsource(RealtimeVoiceSession)
        build = src.index("_system_prompt(agent_name, agent_role, language)")
        branch = src.index('if engine == "azure_realtime"')
        self.assertLess(build, branch, "Prompt muss VOR der Engine-Weiche gebaut werden")
        self.assertEqual(src.count("system_prompt=sys_prompt"), 2,
                         "beide Engines muessen denselben Prompt bekommen")


if __name__ == "__main__":
    unittest.main()
