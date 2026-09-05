"""Nebenlaeufigkeit gehoert ans pids-Budget gebunden — und ein Lauf, dem die
Prozesse ausgehen, darf nicht als erledigt gelten (Issue #628).

Gemessen im Agent-Container: ``pids.max`` steht auf 512, ein voll
hochgefahrener Lauf kostet rund 88 Threads (11 MCP-Server plus der
CLI-Prozess). Bei fuenf gleichzeitigen Laeufen war die Grenze exakt erreicht —
mit null Reserve fuer ``gh``, ``git`` oder ``pytest``. Ab da scheitert jedes
Werkzeug mit ``EAGAIN``, der Lauf merkt es nicht und meldet Erfolg. Mehrere
delegierte Aufgaben kamen so als ``completed`` zurueck, ohne dass ein PR, ein
Kommentar oder eine Datei existierte.
"""

import unittest

from app.pids_budget import (
    COST_PER_RUN_GEMEINSAM,
    DEFAULT_COST_PER_RUN,
    DEFAULT_RESERVE,
    RESERVE_GEMEINSAMER_MCP,
    FALLBACK_MAX_CONCURRENT,
    exhaustion_message,
    find_fork_exhaustion,
    max_concurrent_runs,
)


class TheBudgetIsMeasuredNotGuessedTests(unittest.TestCase):
    """Die Zahlen hier gelten fuer EINZELN laufende MCP-Server.

    Ohne das Pinnen unten liest ``max_concurrent_runs`` ``MCP_HTTP_PORT`` aus der
    Umgebung des Testlaufs. Im Agent-Container ist die Variable gesetzt, also
    rechnet die Funktion mit 8 statt 88 Threads und liefert 47 statt 4 — auf dem
    CI-Runner, wo sie fehlt, bleibt derselbe Test gruen. Ein Test, dessen
    Ergebnis davon abhaengt, WO er laeuft, misst nicht den Code.
    """

    def setUp(self):
        import os
        from unittest import mock

        patcher = mock.patch.dict(os.environ, {"MCP_HTTP_PORT": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_measured_container_allows_four_runs(self):
        """(512-120)/88 = 4,45 -> 4. Nicht die 5, bei denen es riss.

        Die Issue nennt an dieser Stelle 3; das ist ein Rechenfehler, ihre
        eigene Formel mit ihrer eigenen Reserve ergibt 4. Die Probe: 4 Laeufe
        kosten 352, die Grundlast 40 — bleiben 120 frei, also genau die
        Reserve, die fuer ``gh``/``git``/``pytest`` gedacht war.
        """
        self.assertEqual(max_concurrent_runs(512), 4)
        self.assertLessEqual(4 * DEFAULT_COST_PER_RUN + 40, 512 - DEFAULT_RESERVE + 40)

    def test_a_bigger_limit_allows_more(self):
        self.assertEqual(max_concurrent_runs(2048), 21)

    def test_it_never_returns_zero(self):
        """Ein Agent, der gar nichts mehr startet, ist schlimmer als ein enger."""
        self.assertEqual(max_concurrent_runs(64), 1)
        self.assertEqual(max_concurrent_runs(0), 1)

    def test_reserve_and_cost_are_adjustable(self):
        self.assertEqual(max_concurrent_runs(512, reserve=0, cost_per_run=128), 4)

    def test_the_defaults_are_the_measured_ones(self):
        self.assertEqual(DEFAULT_RESERVE, 120)
        self.assertEqual(DEFAULT_COST_PER_RUN, 88)

    def test_gemeinsame_server_machen_aus_vier_laeufen_siebenundvierzig(self):
        """Der Sprung, um den es bei #638 ging — und der Grund fuer das Pinnen.

        Laufen die eingebauten Server in EINEM Prozess, gehoeren sie zur
        Grundlast statt zu jedem Lauf: (512-130)/8 = 47.
        """
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"MCP_HTTP_PORT": "8790"}):
            self.assertEqual(max_concurrent_runs(512), 47)
        self.assertEqual(
            (512 - DEFAULT_RESERVE - RESERVE_GEMEINSAMER_MCP) // COST_PER_RUN_GEMEINSAM,
            47,
        )

    def test_an_unreadable_limit_falls_back_instead_of_crashing(self):
        """Kein Linux, cgroup v1 ohne die Datei, keine Rechte — alles moeglich."""
        self.assertEqual(
            max_concurrent_runs(None, reserve=0, cost_per_run=0),
            FALLBACK_MAX_CONCURRENT,
        )

    def test_a_nonsense_cost_does_not_divide_by_zero(self):
        self.assertEqual(max_concurrent_runs(512, cost_per_run=0), FALLBACK_MAX_CONCURRENT)


class TheKernelMessagesAreRecognisedTests(unittest.TestCase):
    """Genau die Zeilen, die auf der Anlage im Protokoll standen."""

    KERNEL_LINES = (
        "/bin/bash: fork: retry: Resource temporarily unavailable",
        "fatal: unable to create threaded lstat: Resource temporarily unavailable",
        "error: cannot fork() for remote-https: Resource temporarily unavailable",
        "runtime: failed to create new OS thread (have 2 already; errno=11)",
        "fatal error: newosproc",
        "BlockingIOError: [Errno 11] Resource temporarily unavailable",
    )

    def test_every_known_line_is_caught(self):
        for line in self.KERNEL_LINES:
            with self.subTest(line=line):
                self.assertIsNotNone(find_fork_exhaustion(line))

    def test_it_returns_the_line_so_a_human_can_see_what_broke(self):
        hit = find_fork_exhaustion("egal\n/bin/bash: fork: retry: Resource temporarily unavailable\negal")
        self.assertIn("fork: retry", hit)

    def test_ordinary_output_is_left_alone(self):
        self.assertIsNone(find_fork_exhaustion("All 42 tests passed"))
        self.assertIsNone(find_fork_exhaustion("fatal: not a git repository"))

    def test_nothing_at_all_is_not_a_failure(self):
        self.assertIsNone(find_fork_exhaustion(""))
        self.assertIsNone(find_fork_exhaustion(None))

    def test_the_reason_names_the_budget_and_the_evidence(self):
        text = exhaustion_message("/bin/bash: fork: retry: Resource temporarily unavailable")
        self.assertIn("pids", text)
        self.assertIn("fork: retry", text)


class TheRunnerRefusesToCallItDoneTests(unittest.TestCase):
    """Der Kern von #628: der stille Leerlauf, der als Erfolg verbucht wurde."""

    def test_a_tool_result_carries_the_evidence(self):
        from app.agent_runner import AgentRunner

        event = {
            "type": "tool_result",
            "is_error": True,
            "content": "/bin/bash: fork: retry: Resource temporarily unavailable",
        }
        self.assertTrue(AgentRunner._fork_evidence_from_event(event))

    def test_a_nested_user_tool_result_is_seen_too(self):
        from app.agent_runner import AgentRunner

        event = {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "is_error": True,
                "content": [{"type": "text", "text": "error: cannot fork() for remote-https"}],
            }]},
        }
        self.assertTrue(AgentRunner._fork_evidence_from_event(event))

    def test_the_model_talking_about_the_bug_is_not_evidence(self):
        """Sonst schiesst sich ein Agent ab, der an genau diesem Fehler arbeitet."""
        from app.agent_runner import AgentRunner

        event = {
            "type": "assistant",
            "message": {"content": [{
                "type": "text",
                "text": "Der Fehler lautet: fork: retry: Resource temporarily unavailable",
            }]},
        }
        self.assertFalse(AgentRunner._fork_evidence_from_event(event))

    def test_a_clean_tool_result_is_not_evidence(self):
        from app.agent_runner import AgentRunner

        event = {"type": "tool_result", "content": "42 files changed"}
        self.assertFalse(AgentRunner._fork_evidence_from_event(event))

    def test_reading_a_logfile_full_of_the_error_is_not_evidence(self):
        """Der haeufigste Fehlalarm: fremde Fehler LESEN heisst nicht, sie zu HABEN.

        Wer ``/shared/platform-errors.log`` oder Container-Protokolle abruft,
        bekommt genau diese Zeilen als Werkzeug-Ergebnis zurueck — erfolgreich.
        Wuerde das zaehlen, koennte niemand mehr Issue #628 untersuchen, ohne
        den eigenen Lauf als gescheitert zu melden.
        """
        from app.agent_runner import AgentRunner

        event = {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "content": [{"type": "text", "text": (
                    "2026-08-21 08:00 ERROR agent-2ad91565 "
                    "/bin/bash: fork: retry: Resource temporarily unavailable\n"
                    "2026-08-21 08:01 ERROR runtime: failed to create new OS thread"
                )}],
            }]},
        }
        self.assertFalse(AgentRunner._fork_evidence_from_event(event))

    def test_a_failed_tool_still_counts(self):
        """Die Gegenprobe: dasselbe Ergebnis, aber als Fehler gemeldet."""
        from app.agent_runner import AgentRunner

        event = {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "is_error": True,
                "content": [{"type": "text", "text": (
                    "/bin/bash: fork: retry: Resource temporarily unavailable"
                )}],
            }]},
        }
        self.assertTrue(AgentRunner._fork_evidence_from_event(event))


class TheConfiguredParallelismIsCappedTests(unittest.TestCase):
    """``MAX_PARALLEL_TASKS`` ist ein Wunsch, kein Versprechen."""

    def test_a_wish_beyond_the_budget_is_cut_down(self):
        import os
        from unittest import mock

        import app.task_consumer as tc

        with mock.patch.dict(os.environ, {"MAX_PARALLEL_TASKS": "10"}), \
                mock.patch.object(tc, "max_concurrent_runs", return_value=3):
            self.assertEqual(tc._max_parallel_tasks(), 3)

    def test_a_modest_wish_is_left_alone(self):
        import os
        from unittest import mock

        import app.task_consumer as tc

        with mock.patch.dict(os.environ, {"MAX_PARALLEL_TASKS": "2"}), \
                mock.patch.object(tc, "max_concurrent_runs", return_value=3):
            self.assertEqual(tc._max_parallel_tasks(), 2)

    def test_the_default_stays_serial(self):
        import os
        from unittest import mock

        import app.task_consumer as tc

        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(tc, "max_concurrent_runs", return_value=3):
            os.environ.pop("MAX_PARALLEL_TASKS", None)
            self.assertEqual(tc._max_parallel_tasks(), 1)

    def test_garbage_in_the_variable_does_not_break_startup(self):
        import os
        from unittest import mock

        import app.task_consumer as tc

        with mock.patch.dict(os.environ, {"MAX_PARALLEL_TASKS": "viele"}), \
                mock.patch.object(tc, "max_concurrent_runs", return_value=3):
            self.assertEqual(tc._max_parallel_tasks(), 1)


if __name__ == "__main__":
    unittest.main()
