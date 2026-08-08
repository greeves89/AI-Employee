"""Wochensynthese (#384) + Auto-Capture (#385) — Stufe 2 und 3 des Second Brain.

Stufe 1 (#157) verknuepft, sobald etwas entsteht. Was fehlte: einmal pro Woche einen
Schritt zurueckzutreten (Synthese), und ueberhaupt erst aufzuheben, was per Telegram
hereinkommt (Capture) statt es im Chatverlauf versickern zu lassen.

Der wichtigste Teil dieser Tests ist NICHT die Fachlogik, sondern die Verzahnung:
beide Bausteine duerfen kein zweites System aufmachen — keinen eigenen Scheduler,
keinen eigenen Speicher, keine zweite Aehnlichkeitsrechnung, keinen vierten
Wissens-Schreibweg.
"""

import re
import unittest
from pathlib import Path

from app.core import capture
from app.services import synthesis_service as syn

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"


class ClusterTests(unittest.TestCase):
    """Cluster = zusammenhaengende Komponenten im vorhandenen Link-Graphen."""

    def test_separate_groups_stay_separate(self):
        got = syn._connected_components(["a", "b", "c", "d"], [("a", "b"), ("c", "d")])
        self.assertEqual([sorted(g) for g in got], [["a", "b"], ["c", "d"]])

    def test_a_chain_becomes_one_group(self):
        got = syn._connected_components(["a", "b", "c"], [("a", "b"), ("b", "c")])
        self.assertEqual(len(got), 1)
        self.assertEqual(sorted(got[0]), ["a", "b", "c"])

    def test_unlinked_item_is_its_own_group(self):
        """Ein Einzelstueck ohne Nachbarn ist ein Thema fuer sich, kein Fehler."""
        got = syn._connected_components(["a", "b", "c"], [("a", "b")])
        self.assertEqual(len(got), 2)

    def test_biggest_group_first(self):
        got = syn._connected_components(
            ["a", "b", "c", "d"], [("a", "b"), ("b", "c")]
        )
        self.assertEqual(len(got[0]), 3)

    def test_edges_to_unknown_items_are_ignored(self):
        """Kanten auf Eintraege ausserhalb des Zeitfensters duerfen nicht crashen."""
        got = syn._connected_components(["a"], [("a", "weg"), ("auch_weg", "a")])
        self.assertEqual(got, [["a"]])


class RenderTests(unittest.TestCase):
    def test_empty_sections_say_so_instead_of_inventing(self):
        out = syn.WeeklySynthesisService._render(
            {"muster": ["X zieht sich durch"], "widersprueche": [], "luecken": [],
             "aktion": "Y klaeren"}, 12, 5,
        )
        self.assertIn("X zieht sich durch", out)
        self.assertIn("Nichts Auffälliges", out)
        self.assertIn("Y klaeren", out)

    def test_missing_action_is_named(self):
        out = syn.WeeklySynthesisService._render({}, 4, 0)
        self.assertIn("Kein klarer Hebel erkennbar", out)

    def test_counts_are_stated(self):
        out = syn.WeeklySynthesisService._render({"aktion": "A"}, 12, 5)
        self.assertIn("12 Einträgen", out)
        self.assertIn("5 semantische Verbindungen", out)


class CaptureDetectTests(unittest.TestCase):
    def test_explicit_request_wins(self):
        self.assertEqual(capture.detect("merk dir das: Kunde X zahlt quartalsweise"), "explicit")
        self.assertEqual(capture.detect("Speichere das bitte"), "explicit")
        self.assertEqual(capture.detect("remember this"), "explicit")

    def test_a_link_is_kept(self):
        self.assertEqual(capture.detect("schau mal https://example.com/artikel"), "link")

    def test_long_text_is_kept(self):
        self.assertEqual(capture.detect("x" * (capture.LONG_TEXT_CHARS + 1)), "long_text")

    def test_normal_chatter_is_not_kept(self):
        for msg in ("wie geht es dir?", "mach mal den Bericht fertig", "danke!", ""):
            with self.subTest(msg=msg):
                self.assertIsNone(capture.detect(msg))

    def test_commands_are_never_kept(self):
        """Auch ein langer Befehl ist eine Anweisung, keine Ablage."""
        self.assertIsNone(capture.detect("/agent " + "x" * 600))
        self.assertIsNone(capture.detect("!status"))

    def test_exactly_at_the_limit_is_not_long_enough(self):
        self.assertIsNone(capture.detect("x" * capture.LONG_TEXT_CHARS))


class CaptureTitleTests(unittest.TestCase):
    def test_link_title_is_the_url(self):
        title = capture.build_title("schau mal https://example.com/a/b hier", "link")
        self.assertEqual(title, "https://example.com/a/b")

    def test_trailing_punctuation_is_stripped(self):
        self.assertEqual(
            capture.build_title("siehe https://example.com/a.", "link"),
            "https://example.com/a",
        )

    def test_text_title_is_the_first_sentence(self):
        title = capture.build_title("Kunde X zahlt quartalsweise. Und noch mehr Text.", "long_text")
        self.assertEqual(title, "Kunde X zahlt quartalsweise.")

    def test_title_is_never_empty(self):
        self.assertTrue(capture.build_title("   ", "long_text"))

    def test_title_is_bounded(self):
        self.assertLessEqual(len(capture.build_title("y" * 500, "long_text")), capture.TITLE_MAX)

    def test_same_link_yields_the_same_title(self):
        """Der Titel ist der Schluessel — zweimal derselbe Link ergaenzt, statt zu doppeln."""
        a = capture.build_title("hier: https://example.com/x", "link")
        b = capture.build_title("nochmal https://example.com/x bitte", "link")
        self.assertEqual(a, b)


class ContentTests(unittest.TestCase):
    def test_origin_is_recorded(self):
        out = capture.build_content("Text", "link", "Telegram")
        self.assertIn("Telegram", out)
        self.assertIn("Link", out)


class NoSecondSystemTests(unittest.TestCase):
    """Der eigentliche Punkt: nichts davon darf neben dem Bestehenden herlaufen."""

    def test_synthesis_has_no_own_scheduler(self):
        src = (ORCH / "app/services/scheduler_service.py").read_text()
        self.assertIn("_synthesis_service", src,
                      "Die Synthese haengt nicht am bestehenden Takt.")
        self.assertNotIn("synthesis_counter", src,
                         "Eigener Zaehler = zweites Uhrwerk fuer dieselbe Frage.")

    def test_synthesis_has_no_own_table(self):
        """Das Ergebnis IST ein Wissenseintrag — sonst braeuchte es Tabelle,
        Migration und eine zweite Anzeige, die auseinanderlaufen kann."""
        src = (ORCH / "app/services/synthesis_service.py").read_text()
        self.assertNotIn("__tablename__", src)
        self.assertNotIn("CREATE TABLE", src)

    def test_synthesis_reuses_the_llm_access(self):
        src = (ORCH / "app/services/synthesis_service.py").read_text()
        self.assertIn("ReflectionService", src)
        self.assertNotIn("anthropic_api_key", src,
                         "Zugang wird zweimal aufgeloest — laeuft garantiert auseinander.")

    def test_synthesis_reuses_the_existing_link_graph(self):
        src = (ORCH / "app/services/synthesis_service.py").read_text()
        self.assertIn("agent_memory_links", src)
        self.assertIn("brain_links", src)
        self.assertNotIn("<=>", src,
                         "Eigene Vektor-Rechnung — der Auto-Linker hat die Kanten schon.")

    def test_one_knowledge_write_path(self):
        """Vier Kopien hiessen vier Stellen, an denen das Einbetten fehlen kann —
        und ein Eintrag ohne Embedding ist da, aber unsichtbar."""
        writers = [
            "app/api/knowledge.py",
            "app/services/reflection_service.py",
            "app/services/synthesis_service.py",
            "app/core/capture.py",
        ]
        for rel in writers:
            src = (ORCH / rel).read_text()
            with self.subTest(file=rel):
                self.assertIn("knowledge_write", src,
                              f"{rel} schreibt Wissen an core.knowledge_write vorbei.")
                self.assertNotIn("SET embedding = CAST", src,
                                 f"{rel} bettet noch selbst ein.")

    def test_capture_never_blocks_delivery(self):
        """Am 2026-08-06 hat genau so ein Beiwerk die Zustellung verhindert."""
        src = (ORCH / "app/telegram/agent_bot.py").read_text()
        block = src.split("async def _maybe_capture")[1].split("\n    async def ")[0]
        self.assertIn("except Exception", block)

    def test_inbox_has_no_own_endpoints(self):
        """Die Inbox ist eine gefilterte Sicht, kein zweiter Zustand."""
        src = (REPO / "frontend/src/components/knowledge/capture-inbox.tsx").read_text()
        self.assertIn("getAllKnowledgeEntries", src)
        self.assertIn("updateKnowledgeEntry", src)
        self.assertNotIn("/capture", src, "Eigener Endpunkt statt der Wissens-API.")

    def test_both_views_open_the_normal_editor(self):
        for rel in ("frontend/src/components/knowledge/capture-inbox.tsx",
                    "frontend/src/components/knowledge/synthesis-view.tsx"):
            with self.subTest(view=rel):
                self.assertIn("onOpenEntry", (REPO / rel).read_text())


class SettingsPathTests(unittest.TestCase):
    """Eine Einstellung braucht VIER Stellen, sonst meldet die Oberflaeche
    „Gespeichert." und es passiert nichts. Dreimal reingefallen (Stimme,
    Verzeichnis-ID, Berechtigungen) — hier festgenagelt."""

    FIELDS = ("synthesis_enabled", "synthesis_weekday", "synthesis_hour")

    def test_allowed_keys(self):
        src = (ORCH / "app/services/settings_service.py").read_text()
        for f in self.FIELDS:
            with self.subTest(field=f):
                self.assertIn(f'"{f}"', src)

    def test_request_schema(self):
        src = (ORCH / "app/schemas/settings.py").read_text()
        for f in self.FIELDS:
            with self.subTest(field=f):
                self.assertIn(f"{f}:", src)

    def test_patch_mapping(self):
        src = (ORCH / "app/api/settings.py").read_text()
        for f in self.FIELDS:
            with self.subTest(field=f):
                self.assertIn(f'"{f}"', src)

    def test_returned_to_the_ui(self):
        """Ueber den Status der Nachtschicht — kein zweiter Statusweg."""
        src = (ORCH / "app/api/reflection.py").read_text()
        self.assertIn('"synthesis"', src)
        self.assertIn("WeeklySynthesisService", src)

    def test_ui_can_switch_it_on(self):
        src = (REPO / "frontend/src/app/settings/view.tsx").read_text()
        self.assertIn("synthesis_enabled", src)
        self.assertIn("Wochensynthese", src)


class ApiSurfaceTests(unittest.TestCase):
    def test_endpoints_from_the_issue_exist(self):
        src = (ORCH / "app/api/brain.py").read_text()
        self.assertIn('@router.get("/syntheses")', src)
        self.assertIn('@router.post("/synthesize-now"', src)

    def test_frontend_wires_both(self):
        src = (REPO / "frontend/src/lib/api.ts").read_text()
        self.assertIn("brain/syntheses", src)
        self.assertIn("brain/synthesize-now", src)

    def test_knowledge_page_offers_both_views(self):
        src = (REPO / "frontend/src/app/knowledge/page.tsx").read_text()
        self.assertIn('"synthesis"', src)
        self.assertIn('"inbox"', src)
        self.assertIn("SynthesisView", src)
        self.assertIn("CaptureInbox", src)


if __name__ == "__main__":
    unittest.main()
