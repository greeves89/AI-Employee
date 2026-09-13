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

import os
import unittest
from unittest import mock

from tests._mcp_umgebung import (
    Sammelprozess,
    StummerPort,
    mcp_modus,
    toter_port,
)

from app.pids_budget import (
    COST_PER_RUN_GEMEINSAM,
    DEFAULT_COST_PER_RUN,
    DEFAULT_RESERVE,
    FALLBACK_MAX_CONCURRENT,
    RESERVE_GEMEINSAMER_MCP,
    exhaustion_message,
    find_fork_exhaustion,
    max_concurrent_runs,
)


class TheBudgetIsMeasuredNotGuessedTests(unittest.TestCase):
    def setUp(self):
        einzeln = mcp_modus(False)
        einzeln.__enter__()
        self.addCleanup(einzeln.__exit__, None, None, None)

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

    def test_an_unreadable_limit_falls_back_instead_of_crashing(self):
        """Kein Linux, cgroup v1 ohne die Datei, keine Rechte — alles moeglich."""
        self.assertEqual(
            max_concurrent_runs(None, reserve=0, cost_per_run=0),
            FALLBACK_MAX_CONCURRENT,
        )

    def test_a_nonsense_cost_does_not_divide_by_zero(self):
        self.assertEqual(max_concurrent_runs(512, cost_per_run=0), FALLBACK_MAX_CONCURRENT)


class TheSharedMcpModeIsActuallyCheaperTests(unittest.TestCase):
    """Der Zweck von #638 Phase 3 — und bis #326 in der Produktion tot.

    Laufen die eingebauten Server GEMEINSAM in einem Prozess, kostet ein Lauf
    nur noch den ``claude``-Prozess. Genau dieser Zweig wurde nie erreicht,
    weil die Aufrufer Vorgabewerte statt ``None`` durchreichten.
    """

    def test_the_same_container_now_holds_many_more_runs(self):
        with mcp_modus(True):
            self.assertEqual(max_concurrent_runs(512), 47)

    def test_the_single_process_mode_stays_at_four(self):
        with mcp_modus(False):
            self.assertEqual(max_concurrent_runs(512), 4)

    def test_the_shared_costs_are_the_measured_ones(self):
        self.assertEqual(COST_PER_RUN_GEMEINSAM, 8)
        self.assertEqual(RESERVE_GEMEINSAMER_MCP, 10)


class TheBudgetAsksTheMachineNotTheIntentionTests(unittest.TestCase):
    """Die teuerste Gegenprobe des ganzen Umbaus.

    ``_start_combined_mcp`` faellt bei einem Fehlschlag auf Einzelprozesse
    zurueck, LAESST ``MCP_HTTP_PORT`` aber gesetzt. Wer der Variablen glaubt,
    rechnet dann mit 8 Threads je Lauf, waehrend real 88 anfallen: 47 Laeufe
    gegen ein 512er-Budget. Ab da scheitert jedes ``gh``/``git`` mit ``EAGAIN``,
    und der Lauf meldet trotzdem Erfolg — die Fehlerklasse, die #638 ueberhaupt
    erst ausgeloest hat.

    Solange die Erkennung tot war (#326), war das folgenlos. Sie ist es nicht
    mehr.
    """

    def setUp(self):
        einzeln = mcp_modus(False)
        einzeln.__enter__()
        self.addCleanup(einzeln.__exit__, None, None, None)

    def test_a_set_port_without_a_process_stays_expensive(self):
        with mock.patch.dict(os.environ, {"MCP_HTTP_PORT": str(toter_port())}):
            self.assertEqual(max_concurrent_runs(512), 4)

    def test_a_silent_port_stays_expensive(self):
        """Es lauscht etwas — aber es ist nicht der Sammelprozess. Genau hier
        haette ein blosses ``connect`` das Budget falsch angehoben."""
        with StummerPort() as port, mock.patch.dict(
            os.environ, {"MCP_HTTP_PORT": str(port)}
        ):
            self.assertEqual(max_concurrent_runs(512), 4)

    def test_an_error_reply_stays_expensive(self):
        with Sammelprozess(kaputt=True) as port, mock.patch.dict(
            os.environ, {"MCP_HTTP_PORT": str(port)}
        ):
            self.assertEqual(max_concurrent_runs(512), 4)

    def test_a_process_serving_nothing_stays_expensive(self):
        with Sammelprozess(routen=[]) as port, mock.patch.dict(
            os.environ, {"MCP_HTTP_PORT": str(port)}
        ):
            self.assertEqual(max_concurrent_runs(512), 4)

    def test_a_live_process_is_what_unlocks_the_cheap_rate(self):
        """Die Gegenprobe zur Gegenprobe: sonst waere ein Budget, das IMMER 4
        sagt, durch alle vier Tests darueber gekommen."""
        with mcp_modus(True):
            self.assertEqual(max_concurrent_runs(512), 47)


class TheOperatorCanStillOverrideTests(unittest.TestCase):
    """Die Auto-Erkennung darf eine ausdrueckliche Vorgabe nicht ueberstimmen."""

    def test_an_explicit_cost_beats_the_detection(self):
        with mcp_modus(True), mock.patch.dict(
            os.environ, {"PIDS_COST_PER_RUN": "88", "PIDS_RESERVE": "120"}
        ):
            self.assertEqual(max_concurrent_runs(512), 4)

    def test_an_empty_variable_is_not_an_override(self):
        """``PIDS_RESERVE=`` heisst "nichts gesetzt", nicht "Reserve 0"."""
        with mcp_modus(False), mock.patch.dict(os.environ, {"PIDS_RESERVE": "  "}):
            self.assertEqual(max_concurrent_runs(512), 4)

    def test_garbage_falls_back_to_the_detection(self):
        with mcp_modus(False), mock.patch.dict(os.environ, {"PIDS_COST_PER_RUN": "viel"}):
            self.assertEqual(max_concurrent_runs(512), 4)


class TheProductionCallersReachTheDetectionTests(unittest.TestCase):
    """Die eigentliche Regression aus #326 — und der Grund, warum sie ein Jahr
    unbemerkt blieb: die vorhandenen Tests ersetzten ``max_concurrent_runs``
    durch eine Attrappe und prueften damit nur den Deckel, nie den Weg dorthin.

    Beide Produktionsaufrufer reichten ``_env_int(name, VORGABE)`` durch, also
    immer eine Zahl. Der Zweig, der den gemeinsamen MCP-Modus erkennt, haengt
    aber an ``None`` — und war damit in der Produktion nicht erreichbar.
    Gerechnet wurde 4 statt 47.
    """

    def test_the_task_consumer_sees_the_shared_mode(self):
        import app.task_consumer as tc

        with mcp_modus(True), \
                mock.patch.dict(os.environ, {"MAX_PARALLEL_TASKS": "99"}), \
                mock.patch("app.pids_budget.read_pids_limits", return_value=(32, 512)):
            self.assertEqual(tc._max_parallel_tasks(), 47)

    def test_the_run_budget_sees_the_shared_mode(self):
        import app.run_budget as rb

        rb.reset_run_budget()
        self.addCleanup(rb.reset_run_budget)
        with mcp_modus(True), \
                mock.patch("app.pids_budget.read_pids_limits", return_value=(32, 512)):
            self.assertEqual(rb.get_run_budget().total, 47)

    def test_the_single_process_mode_still_caps_hard(self):
        """Die Gegenprobe: ohne gemeinsamen Modus bleibt es bei vier."""
        import app.task_consumer as tc

        with mcp_modus(False), \
                mock.patch.dict(os.environ, {"MAX_PARALLEL_TASKS": "99"}), \
                mock.patch("app.pids_budget.read_pids_limits", return_value=(32, 512)):
            self.assertEqual(tc._max_parallel_tasks(), 4)


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
