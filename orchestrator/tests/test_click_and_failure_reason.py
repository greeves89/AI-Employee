"""Klicks auf dem richtigen Bildschirm — und der echte Grund, wenn etwas scheitert.

Zwei Meldungen vom 21.08.2026, kurz nacheinander:

1. „das ging tatsaechlich voll daneben du hast gerade einfach nur meinen coding
   agent eingestellt" — der Agent klickte auf Bildschirm 2 und traf etwas
   anderes. In der Aktivitaetsspalte stand ``display: 2.0, x: 123.0, y: 456.0``:
   das sind die BEISPIELWERTE aus der Werkzeugbeschreibung. Er hat die
   Koordinaten geraten.
2. „wieso kann der den nicht auswerten? -.-" — die Stimme sagte immer nur „die
   Auswertung kam nicht zurueck". Der echte Grund lag im Klartext vor:
   ``[Fehler: You've hit your limit · resets 3:10pm]`` — das Kontingent des
   Agenten war aufgebraucht. Die Meldung wurde weggeworfen, und der Nutzer
   suchte eine halbe Stunde bei den Bildern.

Dazu eine dritte, eigene Luecke: der Versatz fuer den zweiten Monitor hing
allein am ZULETZT aufgenommenen Screenshot. Wer „klick auf Bildschirm zwei"
sagt, ohne dass unmittelbar davor ein Screenshot genau dieses Bildschirms lief,
klickte mit dem Versatz des falschen Monitors.

Geprueft wird der ECHTE Ablauf: der Dispatcher der Bridge mit einer Attrappe
fuer die Bildschirmliste, die Sprachsitzung mit einer Attrappe fuer die
Agenten-Antwort. Ein auskommentierter Aufruf oder ein vertauschter Zweig faellt
so durch — anders als bei einem Zeichenfenster im Quelltext (#726).
"""

import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

WURZEL = Path(__file__).resolve().parents[2]
BRIDGE_PFAD = WURZEL / "computer-use-bridge/bridge.py"
BRIDGE = BRIDGE_PFAD.read_text()
VOICE = (WURZEL / "orchestrator/app/services/realtime_voice_session.py").read_text()

#: Zwei Monitore, der zweite links oben neben dem ersten — sein Ursprung liegt
#: NICHT bei 0/0. Genau der Fall, in dem ein fehlender Versatz danebenklickt.
BILDSCHIRME = [
    {"number": 1, "primary": True, "x": 0, "y": 0, "width": 1440, "height": 900},
    {"number": 2, "primary": False, "x": -1920, "y": -180, "width": 1920, "height": 1080},
]


_BRIDGE_MODUL = None


def _lade_bridge():
    """bridge.py laden — mit einer Attrappe fuer ``websockets``, falls das Paket
    fehlt (es ist nur fuer die Verbindung noetig, nicht fuer den Dispatcher)."""
    global _BRIDGE_MODUL
    if _BRIDGE_MODUL is not None:
        return _BRIDGE_MODUL
    if "websockets" not in sys.modules:
        try:
            import websockets  # noqa: F401
        except ImportError:
            sys.modules["websockets"] = types.ModuleType("websockets")
    spec = importlib.util.spec_from_file_location("bridge_under_test_click", BRIDGE_PFAD)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    _BRIDGE_MODUL = modul
    return modul


def _funktion(src: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Der Funktionsknoten ``def <name>`` — als AST, nicht als Zeichenzahl."""
    for knoten in ast.walk(ast.parse(src)):
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)) and knoten.name == name:
            return knoten
    raise AssertionError(f"def {name}: nicht gefunden")


class AClickKnowsWhichScreenItMeansTests(unittest.TestCase):
    def setUp(self):
        self.bridge = _lade_bridge()
        self.d = self.bridge.CommandDispatcher.__new__(self.bridge.CommandDispatcher)
        self.d._coord_scale = (1.0, 1.0)
        # Versatz des ZULETZT aufgenommenen Screenshots — absichtlich ein Wert,
        # der zu keinem der beiden Bildschirme passt, damit er sich von einem
        # echten Bildschirmversatz unterscheiden laesst.
        self.d._coord_offset = (7, 9)

    def test_a_named_display_sets_the_offset(self):
        with patch.object(self.bridge, "list_displays", return_value=BILDSCHIRME) as liste:
            self.assertEqual(self.d._display_offset(2), (-1920, -180))
            self.assertEqual(self.d._display_offset("2"), (-1920, -180))
            self.assertEqual(self.d._display_offset(1), (0, 0))
        # Der Versatz kommt aus der LIVE-Bildschirmliste, nicht aus einem
        # gemerkten Zustand — sonst stimmt er nach dem Umstecken nicht mehr.
        self.assertGreaterEqual(liste.call_count, 3)

    def test_it_beats_the_last_screenshot(self):
        """Ausdruecklich genannt schlaegt zuletzt gesehen — sonst haengt der
        Klick an einem Zustand, den der Nutzer nicht sieht."""
        with patch.object(self.bridge, "list_displays", return_value=BILDSCHIRME):
            self.assertEqual(self.d._to_click_space(10, 20, 2), (-1910, -160))
            # Ohne Angabe bleibt der Versatz des letzten Screenshots.
            self.assertEqual(self.d._to_click_space(10, 20), (17, 29))
            self.assertEqual(self.d._to_click_space(10, 20, None), (17, 29))
            # Eine Nummer, die es nicht gibt, faellt auf den letzten Screenshot
            # zurueck statt auf 0/0 — das waere ein dritter, falscher Raum.
            self.assertEqual(self.d._to_click_space(10, 20, 7), (17, 29))

    def test_every_pointer_action_passes_it_through(self):
        """Klicken, Bewegen, Scrollen und Ziehen — eine ausgelassene Stelle
        waere genau der Fall, der spaeter danebengeht."""
        self.assertEqual(BRIDGE.count('self._to_click_space(params["x"], params["y"], params.get("display"))'), 3)
        self.assertIn('params["x1"], params["y1"], params.get("display")', BRIDGE)

    def test_every_pointer_action_lands_on_the_named_screen(self):
        """Dasselbe als Ablauf: jede Zeigeraktion mit ``display=2`` kommt beim
        Eingabe-Controller mit dem Versatz von Bildschirm 2 an."""
        gesehen = []

        class _Aufzeichnung:
            def click(self, x, y, button="left", double=False):
                gesehen.append(("click", x, y))

            def scroll(self, x, y, amount):
                gesehen.append(("scroll", x, y))

            def move(self, x, y):
                gesehen.append(("move", x, y))

            def drag(self, x1, y1, x2, y2, duration=0.3):
                gesehen.append(("drag", x1, y1, x2, y2))

        self.d._ctrl = _Aufzeichnung()
        with patch.object(self.bridge, "list_displays", return_value=BILDSCHIRME):
            for aktion in ("click", "scroll", "move"):
                self.assertEqual(
                    self.d.dispatch({"action": aktion, "params": {"x": 10, "y": 20, "display": 2}}),
                    {"ok": True})
            self.assertEqual(
                self.d.dispatch({"action": "drag", "params": {
                    "x1": 10, "y1": 20, "x2": 30, "y2": 40, "display": 2}}),
                {"ok": True})
        self.assertEqual(gesehen, [
            ("click", -1910, -160),
            ("scroll", -1910, -160),
            ("move", -1910, -160),
            ("drag", -1910, -160, -1890, -140),
        ])
        # Ohne Nummer gilt der gemerkte Versatz UND der Maszstab — auch hier
        # laeuft jede Aktion durch die Umrechnung, nicht roh durch.
        gesehen.clear()
        self.d._coord_scale = (2.0, 3.0)
        with patch.object(self.bridge, "list_displays", side_effect=AssertionError("nicht erwartet")):
            for aktion in ("click", "scroll", "move"):
                self.d.dispatch({"action": aktion, "params": {"x": 10, "y": 20}})
            self.d.dispatch({"action": "drag", "params": {"x1": 10, "y1": 20, "x2": 30, "y2": 40}})
        self.assertEqual(gesehen, [
            ("click", 27, 69),
            ("scroll", 27, 69),
            ("move", 27, 69),
            ("drag", 27, 69, 67, 129),
        ])

    def test_without_a_display_nothing_changes(self):
        """Ein Aufrufer ohne Bildschirmangabe muss sich verhalten wie bisher —
        und darf die Bildschirmliste gar nicht erst anfassen."""
        def _nicht_erwartet():
            raise AssertionError("list_displays() ohne Bildschirmangabe aufgerufen")

        with patch.object(self.bridge, "list_displays", side_effect=_nicht_erwartet):
            for leer in (None, 0, ""):
                with self.subTest(display=leer):
                    self.assertIsNone(self.d._display_offset(leer))
                    self.assertEqual(self.d._to_click_space(10, 20, leer), (17, 29))

    def test_the_early_exit_sits_at_the_top_of_the_function(self):
        """``if not display: return None`` ist der FRUEHAUSSTIEG — eine direkte
        Anweisung der Funktion, nicht etwas, das erst in einer Schleife oder
        einem try-Zweig greift. Ein Blocktext beantwortet nur „steht es drin",
        nicht „auf welcher Ebene" — deshalb die AST-Struktur."""
        fn = _funktion(BRIDGE, "_display_offset")

        def _prueft_display(knoten):
            return any(isinstance(n, ast.Name) and n.id == "display" for n in ast.walk(knoten))

        def _gibt_none_zurueck(anweisung):
            return (isinstance(anweisung, ast.Return)
                    and (anweisung.value is None
                         or (isinstance(anweisung.value, ast.Constant) and anweisung.value.value is None)))

        fruehausstiege = [
            i for i, knoten in enumerate(fn.body)
            if isinstance(knoten, ast.If) and _prueft_display(knoten.test)
            and len(knoten.body) == 1 and _gibt_none_zurueck(knoten.body[0])
        ]
        self.assertTrue(fruehausstiege, "kein `if <display leer>: return None` direkt im Rumpf von _display_offset")
        # ... und zwar BEVOR die Bildschirmliste geholt wird.
        aufrufe = [
            i for i, knoten in enumerate(fn.body)
            if any(isinstance(n, ast.Call) and ast.get_source_segment(BRIDGE, n.func) == "list_displays"
                   for n in ast.walk(knoten))
        ]
        self.assertTrue(aufrufe, "list_displays() wird in _display_offset nicht aufgerufen")
        self.assertLess(fruehausstiege[0], aufrufe[0])

    def test_the_voice_forwards_the_display_on_a_click(self):
        self.assertIn('params["display"] = int(display)', VOICE)


class TheModelIsToldNotToGuessTests(unittest.TestCase):
    """Die geratenen 123/456 stammten aus der Beispielzeile der
    Werkzeugbeschreibung — das Modell hat sie schlicht uebernommen.

    Geprueft wird die Werkzeugbeschreibung, die das Modell WIRKLICH bekommt
    (``DESKTOP_TOOL``), absatzweise: jede ``action='…'``-Erklaerung ist ein
    eigener Absatz, die Hinweise stehen jeweils in ihrem Absatz."""

    @classmethod
    def setUpClass(cls):
        from app.services.realtime_voice_session import DESKTOP_TOOL
        cls.beschreibung = DESKTOP_TOOL["toolSpec"]["description"]

    def _absatz(self, anfang: str) -> str:
        """Der Absatz der Beschreibung, der mit ``anfang`` beginnt — bis zum
        naechsten Zeilenumbruch."""
        for zeile in self.beschreibung.split("\n"):
            if zeile.startswith(anfang):
                return zeile
        raise AssertionError(f"Absatz {anfang!r} fehlt in der Werkzeugbeschreibung")

    def _aktion(self, name: str) -> str:
        """Die Erklaerung zu ``action='<name>'`` — bis zur naechsten
        ``action='``-Erklaerung, egal ob im selben Absatz oder im naechsten."""
        marke = f"action='{name}'"
        self.assertIn(marke, self.beschreibung)
        rest = self.beschreibung.split(marke, 1)[1]
        return rest.split("action='", 1)[0]

    def test_guessing_coordinates_is_forbidden_in_plain_words(self):
        self.assertIn("KOORDINATEN NIEMALS RATEN", self._aktion("click"))

    def test_it_says_where_valid_coordinates_come_from(self):
        """Ein Verbot ohne Bezugsquelle laesst das Modell ratlos — und dann
        raet es wieder."""
        klick = self._aktion("click")
        verbot = klick.split("KOORDINATEN NIEMALS RATEN", 1)
        self.assertEqual(len(verbot), 2, "das Verbot steht nicht in der click-Erklaerung")
        self.assertIn("`find`", verbot[1])
        self.assertIn("Screenshot", verbot[1])

    def test_the_two_screen_order_is_spelled_out(self):
        absatz = self._absatz("MEHRERE BILDSCHIRME:")
        self.assertIn("display=N", absatz)
        self.assertIn("`screenshot`", absatz)
        self.assertIn("`click`", absatz)


class AFailedAnalysisNamesItsReasonTests(unittest.IsolatedAsyncioTestCase):
    """Die Auswertung laeuft nebenher; geprueft wird die Meldung, die danach
    in die Stimme eingespeist wird — mit einer Attrappe fuer die Antwort des
    Agenten."""

    async def _meldung(self, antwort) -> str:
        import app.services.realtime_voice_session as rvs
        v = rvs.RealtimeVoiceSession.__new__(rvs.RealtimeVoiceSession)
        v.agent_id = "agent-1"
        v.redis = AsyncMock()
        v._closed = False
        v._nova = object()
        v._inject_when_quiet = AsyncMock(return_value=True)
        if isinstance(antwort, Exception):
            # Eine gescheiterte Auswertung darf nichts reissen — sie wird
            # protokolliert und wie „nichts zurueck" behandelt.
            with patch.object(rvs, "ask_agent_via_chat", new=AsyncMock(side_effect=antwort)), \
                    self.assertLogs(rvs.logger, level="WARNING"):
                await v._analyse_screenshot_bg("abc", "was siehst du?", "darwin")
        else:
            with patch.object(rvs, "ask_agent_via_chat", new=AsyncMock(return_value=antwort)):
                await v._analyse_screenshot_bg("abc", "was siehst du?", "darwin")
        v._inject_when_quiet.assert_awaited_once()
        return v._inject_when_quiet.await_args[0][0]

    async def test_the_reason_is_extracted_not_discarded(self):
        msg = await self._meldung("[Fehler: You've hit your limit · resets 3:10pm]")
        self.assertIn("You've hit your limit · resets 3:10pm", msg)
        # Als Wortlaut, nicht als rohe Fehlerzeile mit Klammern.
        self.assertNotIn("[Fehler", msg)
        self.assertNotIn("3:10pm]", msg)

    async def test_the_model_is_told_to_pass_it_on(self):
        msg = await self._meldung("[Fehler: You've hit your limit · resets 3:10pm]")
        self.assertIn("Sag ihm diesen Grund", msg)

    async def test_it_no_longer_asks_the_user_what_he_sees_when_the_reason_is_known(self):
        """Genau das war die Reibung: der Nutzer wurde zurueckgefragt, obwohl
        die Ursache im Klartext vorlag."""
        msg = await self._meldung("[Fehler: You've hit your limit · resets 3:10pm]")
        self.assertIn("frage nicht, was er sieht", msg)
        self.assertNotIn("ohne Begruendung", msg)

    async def test_without_a_reason_the_old_wording_stays(self):
        """Kam wirklich nichts zurueck, bleibt die Rueckfrage richtig."""
        for antwort in ("", "[Fehler]", RuntimeError("Verbindung weg")):
            with self.subTest(antwort=antwort):
                msg = await self._meldung(antwort)
                self.assertIn("ohne Begruendung", msg)
                self.assertIn("frage, was er sieht", msg)
                self.assertNotIn("Sag ihm diesen Grund", msg)
                self.assertNotIn("Wortlaut", msg)


if __name__ == "__main__":
    unittest.main()
