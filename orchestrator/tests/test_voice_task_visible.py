"""Jede Aufgabe, die im Gespraech entsteht, muss auch SICHTBAR werden.

Kundenmeldung 2026-08-05: „Alles klar, ich hab die Aufgabe eingeplant" — und im
Panel „Aufgaben & Aktivitaet" blieb es leer. Die Aufgabe lief tatsaechlich
(`tmzdqpquz`, RUNNING), aber `_plan_task` hatte dem Frontend nie ein
`delegate`-Event geschickt. Nur der Sofort-Weg tat das. Der Nutzer fragte
dreimal nach, ob die Aufgabe ueberhaupt existiert.

Die Regel, die diese Tests festhalten: Es gibt GENAU EINE Anmeldestelle
(`_register_task`), und jeder Weg, auf dem Arbeit entsteht, geht durch sie.
Damit steht ein spaeter gebauter dritter Weg nicht wieder stumm da.
"""

import ast
import asyncio
import inspect
import json
import textwrap
import unittest
from unittest.mock import AsyncMock

from app.services.realtime_voice_session import RealtimeVoiceSession


def _method_src(name: str) -> str:
    return inspect.getsource(getattr(RealtimeVoiceSession, name))


def _calls_in(name: str) -> set[str]:
    """Namen aller Methodenaufrufe (self.x(...)) im Quelltext einer Methode."""
    tree = ast.parse(textwrap.dedent(_method_src(name)))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
                found.add(node.func.attr)
    return found


class TaskRegistrationTests(unittest.TestCase):
    def test_plan_task_announces_the_task(self):
        """Der Weg, der den Fehler hatte: einplanen ohne jede Meldung ans Cockpit."""
        self.assertIn("_register_task", _calls_in("_plan_task"))

    def test_immediate_delegation_announces_too(self):
        """Der Sofort-Weg meldet ueber dieselbe Stelle an — nicht mit eigenem _emit."""
        self.assertIn("_register_task", _calls_in("_delegate_and_report"))

    def test_registration_is_the_single_place_that_emits_delegate(self):
        """Kern der Nachhaltigkeit: nur EINE Methode sendet das delegate-Event.

        Sendet ein Weg es wieder selbst, faellt er beim naechsten Umbau raus —
        genau so entstand der Fehler.
        """
        src = inspect.getsource(RealtimeVoiceSession)
        emitters = [ln for ln in src.splitlines() if '"type": "delegate"' in ln]
        self.assertEqual(
            len(emitters), 1,
            "Das delegate-Event darf nur in _register_task entstehen, gefunden: "
            f"{len(emitters)} Stellen",
        )
        self.assertIn('"type": "delegate"', _method_src("_register_task"))

    def test_watching_is_opt_in_for_scheduled_work_only(self):
        """Nur der eingeplante Weg braucht den Rueckkanal — der Sofort-Weg wartet
        selbst auf sein Ergebnis und wuerde sonst doppelt melden."""
        reg = _method_src("_register_task")
        self.assertIn("watch", reg)
        self.assertIn("_watch_planned_tasks", reg)
        self.assertIn("watch=True", _method_src("_plan_task"))
        self.assertNotIn("watch=True", _method_src("_delegate_and_report"))


class TaskResultVisibilityTests(unittest.TestCase):
    def test_finished_task_surfaces_its_files(self):
        """Der Agent legte die PDF brav in /workspace/transfer — und niemand sah sie,
        weil nur der Sofort-Weg den Datei-Scan ausloeste."""
        self.assertIn("_surface_new_files", _calls_in("_voice_task_done"))

    def test_finished_task_sends_its_result_to_the_panel(self):
        """Ohne Ergebnis auf der Karte bleibt „fertig" eine leere Behauptung."""
        self.assertIn('"result"', _method_src("_voice_task_done"))

    def test_delivered_files_become_cards(self):
        """Der Agent liefert seine Deliverables fertig beschriftet mit — echte
        Nutzlast vom Pi, mitgeschnitten auf task:completions (Task tmzdqpquz)."""
        sess = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
        sess._closed = False
        sess._nova = object()
        sess._shown_files = set()
        sess._emit = AsyncMock()
        sess._surface_new_files = AsyncMock()
        sess._inject_when_quiet = AsyncMock()
        asyncio.run(sess._voice_task_done("tmzdqpquz", "PDF der letzten drei Tage", {
            "status": "completed",
            "result": "Fertig — die PDF wurde erstellt.",
            "presented_files": [{
                "path": "/workspace/transfer/Aktivitaetsbericht_2026-08-03_bis_05.pdf",
                "filename": "Aktivitaetsbericht_2026-08-03_bis_05.pdf",
                "media_type": "application/pdf",
                "caption": "Aktivitätsbericht: Was ich die letzten drei Tage gemacht habe",
            }],
        }))
        cards = [c.args[0] for c in sess._emit.call_args_list
                 if c.args[0].get("type") == "media"]
        self.assertEqual(len(cards), 1, "Die gelieferte PDF muss als Karte erscheinen")
        data = cards[0]["data"]
        self.assertEqual(data["filename"], "Aktivitaetsbericht_2026-08-03_bis_05.pdf")
        self.assertEqual(data["media_type"], "application/pdf")
        self.assertIn("letzten drei Tage", data["caption"])

    def test_delivered_files_are_not_shown_twice(self):
        """Der Ordner-Scan laeuft zusaetzlich — dieselbe Datei darf nicht doppelt kommen."""
        sess = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
        sess._closed = False
        sess._nova = object()
        sess._shown_files = {"/workspace/transfer/bericht.pdf"}
        sess._emit = AsyncMock()
        sess._surface_new_files = AsyncMock()
        sess._inject_when_quiet = AsyncMock()
        asyncio.run(sess._voice_task_done("t1", "Bericht", {
            "status": "completed",
            "presented_files": [{"path": "/workspace/transfer/bericht.pdf"}],
        }))
        media = [c for c in sess._emit.call_args_list if c.args[0].get("type") == "media"]
        self.assertEqual(media, [])


class TaskSelfKnowledgeTests(unittest.TestCase):
    def test_summary_counts_scheduled_tasks(self):
        """„guck mal in die Aufgabe rein" → „hab noch keine delegiert", waehrend
        genau diese Aufgabe lief: die Uebersicht kannte nur den Sofort-Weg."""
        src = _method_src("_delegated_tasks_summary")
        self.assertIn("_planned", src)

    def test_summary_denies_only_when_truly_empty(self):
        sess = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
        sess._delegations = []
        sess._planned = {}
        self.assertIn("noch keine Aufgabe", asyncio.run(sess._delegated_tasks_summary()))

        sess._planned = {"tmzdqpquz": "PDF der letzten drei Tage"}
        # Der DB-Stand ist Beiwerk — faellt er aus, muss die Aufgabe trotzdem genannt
        # werden. Genau das war der Fehler: aus „kein Detail" wurde „keine Aufgabe".
        sess._planned_task_state = AsyncMock(return_value=("RUNNING", "nutzt gerade Bash"))
        summary = asyncio.run(sess._delegated_tasks_summary())
        self.assertNotIn("noch keine Aufgabe", summary)
        self.assertIn("PDF der letzten drei Tage", summary)

    def test_summary_reports_the_real_progress(self):
        """„Wie ist der aktuelle Stand?" — dafuer reicht „laeuft" nicht."""
        sess = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
        sess._delegations = []
        sess._planned = {"tmzdqpquz": "Pitchdeck umarbeiten"}
        sess._planned_task_state = AsyncMock(return_value=("RUNNING", "nutzt gerade Bash"))
        summary = asyncio.run(sess._delegated_tasks_summary())
        self.assertIn("RUNNING", summary)
        self.assertIn("nutzt gerade Bash", summary)


class TaskPromiseTests(unittest.TestCase):
    """Zusagen ist nicht Erledigen.

    Kundenmeldung 2026-08-05: „nimm das als Aufgabe mit" → „Ich erstelle dir gleich
    einen Plan dafuer und melde mich" — und es entstand NICHTS. Erst auf das Wort
    „delegiert" lief `plan_task`. Der Nutzer musste die Vokabel des Systems raten.
    """

    def setUp(self):
        from app.services.realtime_voice_session import _system_prompt
        self.p = _system_prompt("TestBot", "Rolle", "de")

    def test_natural_phrasings_trigger_planning(self):
        """Der Nutzer sagt es in SEINEN Worten, nicht in denen des Systems."""
        for phrase in ("nimm das als Aufgabe mit", "setz dir die Aufgabe",
                       "erstell dafür eine Aufgabe", "mach dir dazu einen Task"):
            self.assertIn(phrase, self.p, f"Auslöser fehlt: {phrase}")

    def test_announcing_without_acting_is_forbidden(self):
        """Die eigentliche Regel — sonst sammelt man ewig Reizwoerter nach."""
        self.assertIn("ZUSAGEN IST NICHT ERLEDIGEN", self.p)
        self.assertIn("IM SELBEN ZUG", self.p)

    def test_asking_back_checks_instead_of_guessing(self):
        """„Hast du die Aufgabe erstellt?" darf nicht geraten werden."""
        self.assertIn("get_delegated_tasks statt zu raten", self.p)


if __name__ == "__main__":
    unittest.main()


class SelfInitiatedNoticeTests(unittest.TestCase):
    """Meldungen, die von selbst kommen, muessen auch GESPROCHEN werden.

    Kundenmeldung 2026-08-05: „Aufgabe wurde WÄHREND einer Sprachausgabe fertig...
    Text wurde erstellt aber nicht per Audio ausgegeben." Die Fertigmeldung fiel in
    eine laufende Ausgabe und wurde an den laufenden Satz angehaengt.
    """

    def test_finished_task_waits_for_a_quiet_moment(self):
        self.assertIn("_inject_when_quiet", _calls_in("_voice_task_done"))

    def test_waiting_is_the_single_place(self):
        """Die Warteschleife lag zweimal kopiert im Modul und die Fertigmeldung
        nutzte keine davon — genau wie beim delegate-Ereignis."""
        src = inspect.getsource(RealtimeVoiceSession)
        raw = [ln for ln in src.splitlines() if "inject_user_text" in ln]
        # Erlaubt bleiben: die Begruessung (3 Varianten — ohne Auftrag, Fortsetzung,
        # normal; da spricht noch niemand), die Fortschritts-Erzaehlung (eigene
        # Taktung) und der eine Aufruf IN der Helferin.
        self.assertLessEqual(
            len(raw), 5,
            "Selbst ausgeloeste Meldungen gehen ueber _inject_when_quiet, nicht direkt: "
            f"{len(raw)} rohe Aufrufe",
        )
        self.assertIn("_last_spoken", _method_src("_inject_when_quiet"))

    def test_waiting_is_bounded(self):
        """Eine Zwischenmeldung darf nie ewig auf Stille warten."""
        self.assertIn("deadline", _method_src("_inject_when_quiet"))

    def test_other_self_initiated_notices_wait_too(self):
        for name in ("_notify_files_bg", "_analyse_screenshot_bg"):
            self.assertIn("_inject_when_quiet", _calls_in(name), f"{name} wartet nicht")


class ShownFileRecallTests(unittest.TestCase):
    """Was ich eingeblendet habe, muss ich auch selbst wiederfinden.

    Zweimal am 2026-08-05: Die Karte mit der PDF lag sichtbar im Panel, und der
    Agent antwortete „ist im Workspace nicht zu finden" — er suchte nur die oberste
    Ebene ab und nahm den Namen buchstabengenau.
    """

    def _sess(self, *paths):
        s = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
        s._shown_files = set(paths)
        return s

    def test_exact_name_is_found(self):
        s = self._sess("/workspace/transfer/OpenWebUI-Watcher_Zusammenfassung.pdf")
        self.assertTrue(s._recall_shown_file("OpenWebUI-Watcher_Zusammenfassung.pdf"))

    def test_hyphen_versus_underscore(self):
        """Der echte Fehlschlag: gesucht mit _, abgelegt mit -."""
        s = self._sess("/workspace/transfer/OpenWebUI-Watcher_Zusammenfassung.pdf")
        self.assertEqual(
            s._recall_shown_file("OpenWebUI_Watcher_Zusammenfassung.pdf"),
            "/workspace/transfer/OpenWebUI-Watcher_Zusammenfassung.pdf",
        )

    def test_umlaut_versus_transliteration(self):
        """Der andere echte Fehlschlag: „Aktivitäts" gesucht, „Aktivitaets" abgelegt."""
        s = self._sess("/workspace/transfer/Aktivitaetsbericht_2026-08-03_bis_05.pdf")
        self.assertTrue(s._recall_shown_file("Aktivitätsbericht"))

    def test_unrelated_query_finds_nothing(self):
        s = self._sess("/workspace/transfer/bericht.pdf")
        self.assertEqual(s._recall_shown_file("Angebot Meier"), "")

    def test_search_asks_memory_first(self):
        self.assertIn("_recall_shown_file", _calls_in("_search_files"))

    def test_listing_tasks_registers_running_ones(self):
        """Fragt der Nutzer nach Aufgaben, gehoeren die laufenden zurueck ins Panel."""
        self.assertIn("_register_task", _calls_in("_fast_tasks"))


class EngineSafeTextTests(unittest.TestCase):
    """Was an die Sprach-Engine geht, muss serialisierbar sein.

    Vorfall 2026-08-05: Nach dem Vorlesen einer PDF beendete Nova Sonic den Stream
    mit „Invalid event bytes". Die Laenge war begrenzt, der Zeicheninhalt nicht —
    Steuerzeichen und kaputte Surrogate aus der Dokument-Extraktion brechen das
    Protokoll, nicht das Modell.
    """

    def test_control_characters_are_removed(self):
        out = RealtimeVoiceSession._engine_safe("Analyse\x00\x07 der\x1b Datei")
        self.assertNotIn("\x00", out)
        self.assertNotIn("\x07", out)
        self.assertNotIn("\x1b", out)
        self.assertIn("Analyse", out)

    def test_newlines_and_tabs_survive(self):
        """Absaetze sind Sinn, nicht Stoerung — sie muessen bleiben."""
        out = RealtimeVoiceSession._engine_safe("Zeile eins\nZeile zwei\tEnde")
        self.assertIn("\n", out)
        self.assertIn("\t", out)

    def test_broken_surrogates_do_not_raise(self):
        """Aus PDF-Extraktion kommen halbe Surrogate — die zerlegen sonst den Stream."""
        out = RealtimeVoiceSession._engine_safe("Text \ud800 mehr")
        self.assertIn("Text", out)
        out.encode("utf-8")  # muss ohne Fehler serialisierbar sein

    def test_long_text_is_capped(self):
        out = RealtimeVoiceSession._engine_safe("x" * 9000, limit=4000)
        self.assertLessEqual(len(out), 4002)

    def test_empty_stays_empty(self):
        self.assertEqual(RealtimeVoiceSession._engine_safe(""), "")

    def test_tool_results_go_through_it(self):
        """Der Weg, auf dem der PDF-Text kam — sonst nuetzt der Filter nichts."""
        self.assertIn("_engine_safe", _method_src("_respond"))

    def test_injections_go_through_it_too(self):
        self.assertIn("_engine_safe", _method_src("_inject_when_quiet"))


class SchedulePauseTests(unittest.TestCase):
    """Wiederkehrende Auftraege muss man per Sprache wirklich anhalten koennen.

    Vorfall 2026-08-05: „du solltest ihn noch pausieren" → „Der OpenWebUI-Watcher
    ist jetzt pausiert." Er war es nicht — alle 11 Zeitplaene standen weiter auf
    enabled. Der Agent hatte `cancel_task` genommen (beendet nur den laufenden
    Durchlauf) und Erfolg gemeldet, weil ihm das richtige Werkzeug fehlte.
    """

    def setUp(self):
        from app.services.realtime_voice_session import _system_prompt
        self.p = _system_prompt("TestBot", "Rolle", "de")

    def test_the_tool_exists(self):
        from app.services.realtime_voice_session import MANAGE_SCHEDULES_TOOL
        spec = MANAGE_SCHEDULES_TOOL["toolSpec"]
        self.assertEqual(spec["name"], "manage_schedules")
        schema = json.loads(spec["inputSchema"]["json"])
        actions = schema["properties"]["action"]["enum"]
        self.assertEqual(set(actions), {"list", "pause", "resume"})

    def test_every_tool_schema_is_a_json_string(self):
        """Nova Sonic erwartet einen STRING. Ein rohes Dict laesst die ganze
        Sitzung mit „Unable to parse input chunk" scheitern — nicht nur das
        betroffene Werkzeug. Genau so war Voice am 2026-08-05 komplett tot."""
        import app.services.realtime_voice_session as m
        checked = 0
        for name in dir(m):
            if not name.endswith("_TOOL"):
                continue
            spec = getattr(m, name).get("toolSpec", {})
            raw = spec.get("inputSchema", {}).get("json")
            if raw is None:
                continue
            self.assertIsInstance(raw, str, f"{name}: Schema muss ein JSON-String sein")
            json.loads(raw)  # muss parsebar sein
            checked += 1
        self.assertGreater(checked, 5, "zu wenige Werkzeuge geprueft")

    def test_the_prompt_routes_pausing_there(self):
        self.assertIn("manage_schedules", self.p)
        self.assertIn("pausier", self.p.lower())

    def test_the_prompt_warns_against_the_wrong_tool(self):
        """Genau der Denkfehler, der zur Falschmeldung fuehrte."""
        self.assertIn("GERADE laufenden Durchlauf", self.p)

    def test_missing_capability_must_be_spoken(self):
        """Die allgemeine Regel — sie deckt auch kuenftige Luecken ab."""
        self.assertIn("WAS DU NICHT KANNST, SAGST DU", self.p)
        self.assertIn("NIEMALS", self.p)

    def test_handler_is_wired(self):
        src = inspect.getsource(RealtimeVoiceSession._handle_tool_use)
        self.assertIn("manage_schedules", src)
        self.assertIn("_manage_schedules", src)
