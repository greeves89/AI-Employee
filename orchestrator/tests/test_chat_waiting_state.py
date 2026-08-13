"""„Thinking..." bleibt stehen, obwohl der Agent durch ist.

Kundenmeldung vom 2026-08-13, woertlich: „wenn ich hintereinander 2 nachrichten
schreibe.... schriebt der oben anscheinend die antwort unten steht dann noch
immer message received... und ganz unten dann thinking und der stop button ist
aktiv, ich denke hier ist noch ein status missmatch, denn der agent ist durch!!!"

Der Beweis stand im Bild: unter der Antwort („1+1 = 2") zeigte die Abschlusszeile
``2.4s · 1 turns``. Das ``done`` war also **angekommen und verarbeitet** — es war
kein verlorenes Ereignis, sondern eine falsche Buchhaltung.

**Die Ursache.** Das Fenster rechnete *eine Nachricht = ein Zug*: beim Senden
``pendingCountRef += 1``, bei ``done`` ``-= 1``, und erst bei 0 hoerte das Warten
auf. Live-Steering faltet aber eine nachgereichte Nachricht in den LAUFENDEN Zug
— die Antwort kommt unter der Kennung der **ersten**, es gibt genau **ein**
``done``. Zwei schnell gesendete Nachrichten hinterliessen den Zaehler also
dauerhaft auf 1: Spinner und Stop-Knopf blieben aktiv, fuer immer.

Derselbe Zaehler lebt im Fenster, **nicht im Gespraech**. Beim Wechsel in ein
anderes Gespraech blieb er stehen, und die Ereignisse des verlassenen Gespraechs
werden von der Faden-Abschottung verworfen — sie konnten ihn nie mehr
herunterzaehlen. Deshalb zeigten am Ende **drei** frisch geoeffnete Gespraeche
gleichzeitig „Thinking...".

**Die Korrektur.** ``done`` beendet den Zug, Punkt. Faengt der Agent doch noch
einen eigenen Zug fuer die zweite Nachricht an, hebt dessen erstes Ereignis die
Anzeige wieder an — der laufende Zug zeigt sich durch seine Ereignisse, nicht
durch eine Zaehlung abgeschickter Nachrichten.

Geprueft wird der Quelltext, weil es fuer das Chatfenster keine Laufzeit-Tests
gibt (siehe test_orchestrator_replies_reach_the_screen.py, gleiches Vorgehen).
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT = (ROOT / "frontend/src/components/agents/chat.tsx").read_text()


def _block(marker: str, laenge: int = 2600) -> str:
    """Der Abschnitt ab einer Fundstelle — grob, aber genau genug fuer die Frage
    „steht das im richtigen Zweig"."""
    return CHAT.split(marker, 1)[1][:laenge]


class DoneEndsTheTurnTests(unittest.TestCase):
    """Der Kern. Alles andere ist Absicherung."""

    def test_done_clears_the_counter_completely(self):
        block = _block('} else if (type === "done") {')
        self.assertIn("pendingCountRef.current = 0;", block)

    def test_done_stops_the_waiting_state(self):
        block = _block('} else if (type === "done") {')
        self.assertIn("setIsWaiting(false);", block)

    def test_it_no_longer_counts_down_per_message(self):
        """Genau diese Zeile war der Fehler — sie darf im done-Zweig nicht
        zurueckkehren."""
        block = _block('} else if (type === "done") {')
        self.assertNotIn("pendingCountRef.current - 1", block)


class StaleSteeringHintsGoTests(unittest.TestCase):
    """„Message received — steering current agent turn" ist Live-Zustand, kein
    Verlauf. Nach dem Zug ist der Satz schlicht unwahr — es laeuft keiner mehr.

    Entfernt wurde er bisher nur, wenn fuer GENAU DIESE Kennung eine
    Antwortnachricht entstand. Eine gefaltete Nachricht bekommt nie eine eigene;
    ihr Hinweis blieb darum fuer immer stehen — im Bild des Kunden gleich
    zweimal, unterhalb der fertigen Antwort."""

    def test_done_removes_them(self):
        block = _block('} else if (type === "done") {')
        self.assertIn("isQueued", block)

    def test_the_removal_walks_backwards(self):
        """Vorwaerts mit splice ueberspringt bei zwei Hinweisen hintereinander
        den zweiten — genau der Fall aus dem Bild."""
        block = _block('} else if (type === "done") {')
        self.assertRegex(block, r"for \(let i = msgs\.length - 1; i >= 0; i--\)")


class AFurtherTurnRaisesTheIndicatorAgainTests(unittest.TestCase):
    """Wenn ``done`` das Warten immer beendet, muss ein WEITERER Zug es wieder
    anheben — sonst laeuft die zweite Antwort ohne jede Anzeige durch."""

    def test_stream_events_raise_it(self):
        self.assertIn(
            'if (type === "text" || type === "tool_call" || type === "tool_result") {',
            CHAT,
        )
        block = _block('if (type === "text" || type === "tool_call" || type === "tool_result") {', 300)
        self.assertIn("setIsWaiting(true)", block)

    def test_it_does_not_fight_the_stop_button(self):
        """Nur anheben, wenn es unten ist — sonst ein Rendern je Ereignis."""
        block = _block('if (type === "text" || type === "tool_call" || type === "tool_result") {', 300)
        self.assertIn("if (!isWaitingRef.current)", block)


class SwitchingConversationsResetsItTests(unittest.TestCase):
    """Drei geoeffnete Gespraeche, alle drei „Thinking..." — weil der Zaehler dem
    Fenster gehoert und nicht dem Gespraech."""

    def test_there_is_a_reset_on_session_change(self):
        self.assertIn("pendingCountRef.current = 0;\n    notBusyStreakRef.current = 0;", CHAT)

    def test_the_reset_hangs_on_the_active_session(self):
        idx = CHAT.find("pendingCountRef.current = 0;\n    notBusyStreakRef.current = 0;")
        self.assertGreater(idx, 0)
        self.assertIn("}, [activeSessionId]);", CHAT[idx:idx + 400])


class TheWatchdogTests(unittest.TestCase):
    """Falls doch einmal ein ``done`` unterwegs verlorengeht (die
    Faden-Abschottung verwirft Fremdes), ist der Agent selbst die Wahrheit."""

    def test_it_exists(self):
        self.assertIn("if (!busy && isWaitingRef.current) {", CHAT)

    def test_it_is_deliberately_slow(self):
        """Ein eiliger Abbruch loescht die Anzeige mitten im Denken. Der Anlauf
        eines Zuges dauert mehrere Sekunden, in denen der Agent noch nicht als
        beschaeftigt gilt."""
        block = _block("if (!busy && isWaitingRef.current) {")
        self.assertIn("notBusyStreakRef.current >= 3", block)
        self.assertRegex(block, r"ruhe > \d{5}")

    def test_sending_counts_as_a_sign_of_life(self):
        """Ohne das schlaegt die Notbremse waehrend des Anlaufs zu: frisch
        gesendet, Agent noch nicht ‚working', drei Runden nicht beschaeftigt."""
        block = _block("pendingCountRef.current += 1;", 400)
        self.assertIn("lastEventAtRef.current = Date.now();", block)

    def test_every_event_counts_as_a_sign_of_life(self):
        idx = CHAT.find("lastEventAtRef.current = Date.now();\n    notBusyStreakRef.current = 0;")
        self.assertGreater(idx, 0, "Ereignisse muessen die Ruhe-Uhr zuruecksetzen")


class TheActiveChatPillCatchesUpTests(unittest.TestCase):
    """Zweite Meldung desselben Tages: „es hat sieben sekunden gebraucht bis
    aktiver chat da war. woran liegt das."

    Nicht am Anlauf des Agenten. Die Anzeige haengt an ``current_task``, und die
    Agentenseite fragt den Agenten alle **15 Sekunden** ab. Wer irgendwann
    sendet, wartet im Mittel die halbe Taktzeit auf die Anzeige: 7,5 Sekunden —
    exakt das Gemessene. Ein kurzer Nachfass-Stoss trifft den Moment, in dem der
    Agent den Auftrag aufnimmt, ohne dauerhaft haeufiger abzufragen."""

    PAGE = (ROOT / "frontend/src/app/agents/[id]/page.tsx").read_text()

    def test_the_chat_can_tell_the_page(self):
        self.assertIn("onTurnChange?: () => void", CHAT)
        self.assertIn("onTurnChange={nachfassen}", self.PAGE)

    def test_sending_triggers_it(self):
        block = _block("pendingCountRef.current += 1;", 700)
        self.assertIn("onTurnChangeRef.current?.()", block)

    def test_the_end_of_a_turn_triggers_it_too(self):
        """Sonst bliebe die Anzeige nach dem Zug bis zu 15 Sekunden stehen."""
        block = _block('if (type === "done" || type === "cancelled" || type === "error") {', 200)
        self.assertIn("onTurnChangeRef.current?.();", block)

    def test_the_burst_is_short_and_bounded(self):
        self.assertIn("[600, 1800, 4000].map((ms) => setTimeout(ladeAgent, ms))", self.PAGE)

    def test_the_burst_cleans_up_after_itself(self):
        """Ohne Aufraeumen sammelt jedes Absenden weitere Zeitgeber an."""
        self.assertIn("nachfassenTimers.current.forEach(clearTimeout)", self.PAGE)

    def test_the_callback_goes_through_a_ref(self):
        """Direkt als Abhaengigkeit wuerde der Ereignis-Verteiler bei jedem
        Rendern der Elternseite neu gebaut — und mit ihm die WS-Verbindung."""
        self.assertIn("const onTurnChangeRef = useRef(onTurnChange);", CHAT)


class NoFalseWorksElsewhereBannerTests(unittest.TestCase):
    """Direkt nach der Korrektur oben gemeldet: „Er war fertig, ploetzlich poppte
    diese Meldung auf, nach ein paar Sekunden war es dann weg."

    Gemeint ist „Agent arbeitet gerade an dieser Unterhaltung...". Sie haengt an
    ``busy && !isWaiting``, und ``busy`` stammt aus einer Abfrage im
    Vier-Sekunden-Takt — unmittelbar nach dem Zugende steht dort noch
    „beschaeftigt". Vorher blieb ``isWaiting`` haengen und verdeckte das
    zufaellig; mit dem korrekten Abraeumen wurde der alte Messwert sichtbar.

    Ein Fehler, den eine Korrektur nicht verursacht, sondern nur aufdeckt — die
    Sperre gilt bewusst NUR fuer den eigenen, gerade beendeten Zug, damit der
    echte Fall (man betritt ein Gespraech, in dem gerade gearbeitet wird)
    weiterhin sofort angezeigt wird."""

    def test_the_banner_is_suppressed_right_after_our_own_turn(self):
        self.assertIn(
            "setLiveElsewhere(busy && !isWaitingRef.current && !frischFertig);", CHAT
        )

    def test_the_end_of_our_turn_is_recorded(self):
        self.assertIn("eigenerZugEndeteRef.current = Date.now();", CHAT)

    def test_the_window_is_longer_than_the_poll_interval(self):
        """Kuerzer als der Takt der Abfrage waere wirkungslos — genau ein
        veralteter Messwert soll abgefangen werden."""
        block = _block("const frischFertig =", 200)
        self.assertRegex(block, r"< (\d{4,})")
        fenster = int(re.search(r"< (\d{4,})", block).group(1))
        self.assertGreater(fenster, 4000, "Abfragetakt liegt bei 4000ms")

    def test_the_legitimate_case_still_works(self):
        """Betritt man ein Gespraech, in dem gerade gearbeitet wird, gab es
        keinen eigenen Zug — die Sperre greift dort nicht."""
        self.assertIn("const eigenerZugEndeteRef = useRef(0);", CHAT)


class WorkInProgressIsVisibleTests(unittest.TestCase):
    """Kundenwunsch (Uhde, 13.08.2026): „Ich wollte im Chat eine Anzeige haben,
    dass noch am Thema gearbeitet wird (in Progress, warte noch auf SubAgents
    Rueckmeldung oder so etwas)."

    Die Luecke: nach dem Delegieren ist der Zug des Agenten BEENDET. Also laeuft
    kein „Thinking..."-Spinner, obwohl die Auftraege noch laufen — der Mensch sah
    nichts und musste nachfragen. Die Kacheln zeigen jede Aufgabe einzeln; hier
    fehlte der Stand in EINER Zeile.
    """

    def test_it_does_not_depend_on_the_agents_own_turn(self):
        """Der springende Punkt: unabhaengig von ``isWaiting``, sonst waere die
        Anzeige genau dann weg, wenn man sie braucht."""
        # Nur der eigene Block — ein zu weites Fenster reicht in den
        # NACHBARBLOCK hinein, der zu Recht auf ``isWaiting`` prueft.
        block = _block("{offeneAuftraege.length > 0 && (", 1600).split("\n        )}", 1)[0]
        self.assertNotIn("isWaiting", block)

    def test_it_counts_only_what_is_still_open(self):
        block = _block("{offeneAuftraege.length > 0 && (", 1600)
        self.assertIn("offeneAuftraege.length}", block)

    def test_it_names_who_is_still_missing(self):
        """„warte noch auf SubAgents" — WER aussteht, nicht nur dass etwas laeuft."""
        block = _block("{offeneAuftraege.length > 0 && (", 1600)
        self.assertIn("assigned_agent_name", block)

    def test_each_colleague_is_named_once(self):
        block = _block("{offeneAuftraege.length > 0 && (", 1600)
        self.assertIn("new Set(", block)

    def test_only_unfinished_orders_count_as_open(self):
        self.assertIn('alleAuftraege.filter((k) => k.phase !== "done")', CHAT)

    def test_a_dismissed_card_leaves_the_count(self):
        """Das Kreuz entfernt den Eintrag aus ``taskCards`` — die Ableitung
        haengt daran, also verschwindet er hier von selbst."""
        self.assertIn("const alleAuftraege = useMemo(() => Object.values(taskCards)", CHAT)


class TheTypeIsRealTests(unittest.TestCase):
    """Ein Zweig auf einen Ereignistyp, den es nicht gibt, ist toter Code. Beim
    Bauen fiel genau das auf (``"thinking"`` steht nicht in der Union)."""

    def test_the_raised_types_are_declared(self):
        union = re.search(r'type:\s*("(?:text|tool_call)"[^;]+);', CHAT)
        self.assertIsNotNone(union)
        for t in ("text", "tool_call", "tool_result", "done", "queued"):
            self.assertIn(f'"{t}"', union.group(1))


if __name__ == "__main__":
    unittest.main()
