"""Mehrere Bildschirme — und das Modell weiss, wie gross das Bild ist.

Nutzerwunsch vom 21.08.2026, woertlich: „WEITERHIN brauch ich bei Screenshot
AUCH ALLE ANDEREN Bildschirme.... und das ich dem Agenten sagen kann geh bitte
auf bildschirm 1 oder 2... ZUSAETZLICH muss der voice und auch agent wissen WIE
GROSS das Bild ist, damit der besser klicken kann!"

Zwei Befunde vorab, beide belegt:

* Die Bridge nahm ausschliesslich den HAUPTbildschirm auf
  (``CGDisplayCreateImage(CGMainDisplayID())``) — ein zweiter Monitor war
  unerreichbar.
* Sie berechnet ``image_size`` seit jeher und gab es auch zurueck. Im
  Orchestrator und im Agenten kam es **nirgends** vor: das Modell nannte
  Klickkoordinaten, ohne zu wissen, wie gross das Bild ueberhaupt ist.

Der zweite Punkt ist der heikle: bei einem Nebenbildschirm liegt der Ursprung
NICHT bei 0/0. Ohne Versatz landet jeder Klick auf dem zweiten Monitor auf dem
ersten.

Geprueft wird der ECHTE Ablauf, wo er sich mit Attrappen fahren laesst (Quartz,
Bildaufnahme, Bildschirmliste); der Agenten-Code, der sich aus dem
Orchestrator heraus nicht importieren laesst, ueber seinen AST-Block mit
getilgten Kommentaren — nicht ueber ein Zeichenfenster (#726).
"""

import ast
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

WURZEL = Path(__file__).resolve().parents[2]
BRIDGE_PFAD = WURZEL / "computer-use-bridge/bridge.py"
BRIDGE = BRIDGE_PFAD.read_text()
VOICE = (WURZEL / "orchestrator/app/services/realtime_voice_session.py").read_text()
MCP = (WURZEL / "agent/mcp/computer-use-server.mjs").read_text()
CUSTOM = (WURZEL / "agent/app/tools/api_client.py").read_text()

#: Zwei Monitore; der zweite haengt links oben und beginnt NICHT bei 0/0.
BILDSCHIRME = [
    {"number": 1, "id": 3, "primary": True, "x": 0, "y": 0, "width": 1440, "height": 900},
    {"number": 2, "id": 5, "primary": False, "x": -1920, "y": -180, "width": 1920, "height": 1080},
]

_BRIDGE_MODUL = None


def _lade_bridge():
    """bridge.py laden — mit einer Attrappe fuer ``websockets``, falls das Paket
    fehlt (es ist nur fuer die Verbindung noetig, nicht fuer die Aufnahme)."""
    global _BRIDGE_MODUL
    if _BRIDGE_MODUL is not None:
        return _BRIDGE_MODUL
    if "websockets" not in sys.modules:
        try:
            import websockets  # noqa: F401
        except ImportError:
            sys.modules["websockets"] = types.ModuleType("websockets")
    spec = importlib.util.spec_from_file_location("bridge_under_test_screens", BRIDGE_PFAD)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    _BRIDGE_MODUL = modul
    return modul


def _ohne_kommentare(block: str) -> str:
    """Kommentare aus einem Quelltextblock tilgen (per tokenize, nicht per
    '#'-Suche). Ein auskommentierter Aufruf stuende sonst weiterhin im Block
    und bestuende jedes `assertIn` — die Blindstelle aus #726."""
    import io
    import textwrap
    import tokenize

    text = textwrap.dedent(block)
    zeilen = text.splitlines(keepends=True)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                (zeile, von), (_, bis) = tok.start, tok.end
                zeilen[zeile - 1] = zeilen[zeile - 1][:von] + zeilen[zeile - 1][bis:]
    except tokenize.TokenError as e:  # unvollstaendiger Block — lieber laut
        raise AssertionError(f"Block nicht tokenisierbar: {e}")
    return "".join(zeilen)


def _knotenquelle(src: str, knoten: ast.AST) -> str:
    """``ast.get_source_segment`` mit korrigierter erster Zeile.

    Die erste Zeile kommt OHNE ihre urspruengliche Einrueckung zurueck (sie
    schneidet ab ``col_offset``), alle folgenden MIT voller Original-
    Einrueckung. Bei einer Geschwister-Klausel auf derselben Spalte (``except``
    zu ``try``) springt eine Zeile dann scheinbar auf Spalte 0 zurueck, ohne
    dass diese Ebene je geoeffnet wurde — ``textwrap.dedent`` findet keinen
    gemeinsamen Praefix mehr. Die fehlende Einrueckung der ersten Zeile hier
    wieder auffuellen, bevor gekuerzt wird."""
    text = ast.get_source_segment(src, knoten) or ""
    return (" " * knoten.col_offset) + text


def _if_block(src: str, bedingung: str) -> str:
    """Der ``if``-Zweig, dessen Bedingung mit ``bedingung`` beginnt — als
    kleinster umschliessender AST-Knoten, Kommentare getilgt."""
    for knoten in ast.walk(ast.parse(src)):
        if isinstance(knoten, ast.If) and (
                ast.get_source_segment(src, knoten.test) or "").startswith(bedingung):
            return _ohne_kommentare(_knotenquelle(src, knoten))
    raise AssertionError(f"if {bedingung}: nicht gefunden")


class _Bild:
    """Ersetzt das PIL-Bild: nur Groesse und ein leeres PNG."""

    def __init__(self, width, height):
        self.width, self.height = width, height

    def save(self, buf, **_kw):
        buf.write(b"png")


def _quartz_attrappe(haupt: int, kennungen: list[int], lagen: dict) -> types.ModuleType:
    """Ein ``Quartz``-Modul mit genau den drei Aufrufen, die list_displays braucht."""
    quartz = types.ModuleType("Quartz")
    quartz.CGGetActiveDisplayList = lambda _max, _a, _b: (0, list(kennungen), len(kennungen))
    quartz.CGMainDisplayID = lambda: haupt

    def _bounds(kennung):
        x, y, w, h = lagen[kennung]
        return types.SimpleNamespace(
            origin=types.SimpleNamespace(x=x, y=y), size=types.SimpleNamespace(width=w, height=h))
    quartz.CGDisplayBounds = _bounds
    return quartz


class TheBridgeSeesEveryScreenTests(unittest.TestCase):
    def setUp(self):
        self.bridge = _lade_bridge()

    def test_it_can_enumerate_the_displays(self):
        self.assertIn("def list_displays(", BRIDGE)
        self.assertIn("CGGetActiveDisplayList", BRIDGE)

    def test_the_primary_screen_is_number_one(self):
        """Das ist die Zaehlweise, die ein Mensch am Telefon benutzt — egal, in
        welcher Reihenfolge Quartz die Kennungen liefert."""
        # Die Hauptkennung ist absichtlich WEDER die erste gemeldete noch die
        # kleinste — sonst kaeme sie auch ohne Sortierung nach vorn, und der
        # Test koennte gar nicht falsch antworten.
        quartz = _quartz_attrappe(
            haupt=5, kennungen=[7, 3, 5],
            lagen={5: (0, 0, 1440, 900), 3: (-1920, -180, 1920, 1080), 7: (1440, 0, 1024, 768)})
        with patch.dict(sys.modules, {"Quartz": quartz}), patch.object(sys, "platform", "darwin"):
            liste = self.bridge.list_displays()
        self.assertEqual([b["number"] for b in liste], [1, 2, 3])
        self.assertEqual(liste[0]["id"], 5)
        self.assertTrue(liste[0]["primary"])
        self.assertEqual([b["primary"] for b in liste[1:]], [False, False])
        self.assertEqual((liste[0]["x"], liste[0]["y"], liste[0]["width"], liste[0]["height"]),
                         (0, 0, 1440, 900))
        # Die uebrigen folgen in Kennungsreihenfolge — stabil ueber Aufrufe.
        self.assertEqual([b["id"] for b in liste], [5, 3, 7])
        self.assertEqual((liste[1]["x"], liste[1]["y"]), (-1920, -180))

    def test_a_specific_screen_can_be_captured(self):
        self.assertIn("def capture_screenshot(scale: float = 1.0, display: int | None = None)", BRIDGE)
        self.assertIn("_capture_macos_inprocess(display_id=None)".replace("=None", ""), BRIDGE.replace("display_id=None", "display_id"))

    def _aufnahme(self, display):
        """capture_screenshot mit Attrappen: Bildschirmliste, In-Prozess-Aufnahme
        (merkt sich die gewuenschte Kennung) und ein PIL, das es hier nicht gibt.

        Die logische Groesse des alten Wegs (``_logical_screen_size``) weicht
        absichtlich vom Listeneintrag des Hauptbildschirms ab — sonst liesse
        sich nicht unterscheiden, welchen Weg der Code genommen hat."""
        aufgenommen = []

        def _inprocess(display_id=None):
            aufgenommen.append(display_id)
            return _Bild(1000, 600)

        pil = types.ModuleType("PIL")
        pil.Image = types.SimpleNamespace(LANCZOS=1)
        with patch.dict(sys.modules, {"PIL": pil}), \
                patch.object(sys, "platform", "darwin"), \
                patch.object(self.bridge, "list_displays", return_value=BILDSCHIRME), \
                patch.object(self.bridge, "_capture_macos_inprocess", new=_inprocess), \
                patch.object(self.bridge, "_logical_screen_size", return_value=(1280, 800)):
            _b64, meta = self.bridge.capture_screenshot(1.0, display)
        return aufgenommen, meta

    def _unbekannte_nummer(self):
        """capture_screenshot(display=3) bei zwei Bildschirmen — liefert die
        Ausnahme und die Attrappe der Aufnahme."""
        pil = types.ModuleType("PIL")
        pil.Image = types.SimpleNamespace(LANCZOS=1)
        with patch.dict(sys.modules, {"PIL": pil}), \
                patch.object(sys, "platform", "darwin"), \
                patch.object(self.bridge, "list_displays", return_value=BILDSCHIRME), \
                patch.object(self.bridge, "_capture_macos_inprocess") as aufnahme, \
                self.assertRaises(ValueError) as cm:
            self.bridge.capture_screenshot(1.0, 3)
        return cm.exception, aufnahme

    def test_an_unknown_screen_number_says_so(self):
        """Lieber eine klare Meldung als stillschweigend den falschen Monitor."""
        fehler, _aufnahme = self._unbekannte_nummer()
        self.assertIn("gibt es nicht", str(fehler))
        self.assertIn("Bildschirm 3", str(fehler))
        self.assertIn("1 bis 2", str(fehler))

    def test_no_picture_is_taken_of_a_screen_that_does_not_exist(self):
        """Die Meldung allein reicht nicht: vorher darf auch kein Bild vom
        falschen Monitor entstehen."""
        _fehler, aufnahme = self._unbekannte_nummer()
        aufnahme.assert_not_called()

    def test_a_named_screen_is_the_one_that_is_captured(self):
        aufgenommen, meta = self._aufnahme(2)
        self.assertEqual(aufgenommen, [5])            # die Quartz-Kennung von Nr. 2
        self.assertEqual(meta["display"], 2)
        # Bei einem Nebenbildschirm gilt SEINE Groesse, nicht die des Haupts.
        self.assertEqual((meta["logical_w"], meta["logical_h"]), (1920, 1080))
        self.assertEqual(meta["displays"], BILDSCHIRME)

    def test_omitting_the_number_keeps_the_old_behaviour(self):
        """Jeder bestehende Aufrufer muss unveraendert weiterlaufen: ohne
        Nummer wird der Hauptbildschirm aufgenommen, die Groesse kommt wie
        bisher von ``_logical_screen_size``."""
        for leer in (None, 0):
            with self.subTest(display=leer):
                aufgenommen, meta = self._aufnahme(leer)
                self.assertEqual(aufgenommen, [None])
                self.assertEqual(meta["display"], 1)
                self.assertEqual((meta["logical_w"], meta["logical_h"]), (1280, 800))


class ClicksLandOnTheRightScreenTests(unittest.TestCase):
    """Der heikelste Teil: der zweite Monitor beginnt nicht bei 0/0."""

    def setUp(self):
        self.bridge = _lade_bridge()
        self.d = self.bridge.CommandDispatcher.__new__(self.bridge.CommandDispatcher)
        self.d._coord_scale = (1.0, 1.0)
        self.d._coord_offset = (0, 0)

    def test_the_offset_is_remembered_after_a_screenshot(self):
        # Die Maszstaebe sind ABSICHTLICH ungleich: mit sx == sy bliebe ein
        # Achsentausch beim Merken unsichtbar (Gegenleser-Fund, Batch 10).
        meta = {
            "image_w": 1280, "image_h": 720, "logical_w": 1920, "logical_h": 900,
            "scale_x": 1.5, "scale_y": 1.25, "display": 2, "displays": BILDSCHIRME,
        }
        with patch.object(self.bridge, "capture_screenshot", return_value=("b64", meta)) as aufnahme:
            antwort = self.d.dispatch({"action": "screenshot", "params": {"display": 2}})
        self.assertEqual(aufnahme.call_count, 1)
        args, kwargs = aufnahme.call_args
        self.assertEqual(list(args[1:]) + list(kwargs.values()), [2])   # die Nummer kommt an
        self.assertEqual(antwort["display"], 2)
        self.assertEqual(antwort["image_size"], {"w": 1280, "h": 720})
        self.assertEqual(antwort["displays"], BILDSCHIRME)
        self.assertEqual(self.d._coord_offset, (-1920, -180))
        self.assertEqual(self.d._coord_scale, (1.5, 1.25))
        # ... und der naechste Klick ohne Bildschirmangabe landet dort.
        self.assertEqual(self.d._to_click_space(100, 100), (-1770, -55))

    def test_a_screenshot_without_a_number_resets_to_the_primary_screen(self):
        """Der Versatz haengt am ZULETZT aufgenommenen Bildschirm — auch wenn
        der Aufrufer keine Nummer nennt. Bliebe er nur bei gesetztem Parameter
        aktuell, saesse nach einem Bild von Monitor 2 jeder Klick auf dem
        Hauptmonitor daneben. Die Antwort meldet dabei den Bildschirm, den die
        Aufnahme WIRKLICH lieferte — kein Echo des Parameters."""
        self.d._coord_offset = (7, 9)          # Fremdwert: passt zu keinem Monitor
        self.d._coord_scale = (2.0, 2.0)
        meta = {
            "image_w": 720, "image_h": 600, "logical_w": 1440, "logical_h": 900,
            "scale_x": 1.5, "scale_y": 2.0, "display": 1, "displays": BILDSCHIRME,
        }
        with patch.object(self.bridge, "capture_screenshot", return_value=("b64", meta)) as aufnahme:
            antwort = self.d.dispatch({"action": "screenshot", "params": {}})
        args, kwargs = aufnahme.call_args
        self.assertEqual(list(args[1:]) + list(kwargs.values()), [None])
        self.assertEqual(antwort["display"], 1)
        self.assertEqual(self.d._coord_offset, (0, 0))
        self.assertEqual(self.d._coord_scale, (1.5, 2.0))
        self.assertEqual(self.d._to_click_space(100, 100), (150, 200))

    def test_the_offset_is_added_when_clicking(self):
        # Ungleiche Maszstaebe je Achse — sonst ist x/y vertauscht unsichtbar.
        self.d._coord_scale = (2.0, 3.0)
        self.d._coord_offset = (100, 50)
        self.assertEqual(self.d._to_click_space(10, 20), (120, 110))
        # Seit dem Klick-Fix schlaegt ein ausdruecklich genannter Bildschirm den
        # zuletzt aufgenommenen — der Versatz kommt weiter aus einem der beiden.
        with patch.object(self.bridge, "list_displays", return_value=BILDSCHIRME):
            self.assertEqual(self.d._to_click_space(10, 20, 2), (-1900, -120))
            self.assertEqual(self.d._to_click_space(10, 20, None), (120, 110))

    def test_fractions_are_rounded_not_truncated(self):
        """Ein Retina-Maszstab liefert Brueche; 33,75 muss 34 werden, nicht 33 —
        sonst driftet jeder Klick um bis zu einen Punkt nach oben links."""
        self.d._coord_scale = (1.125, 1.375)
        self.d._coord_offset = (0, 0)
        self.assertEqual(self.d._to_click_space(30, 30), (34, 41))   # 33.75 / 41.25
        self.assertEqual(self.d._to_image_space(34, 41), (30, 30))   # 30.22 / 29.82

    def test_and_subtracted_on_the_way_back(self):
        """Sonst waeren die beiden Richtungen nicht mehr Umkehrungen
        voneinander — und `find_element` klickte daneben. Die direkte
        Zusicherung ist noetig: der Rundlauf allein bliebe auch dann gruen,
        wenn BEIDE Richtungen die Achsen vertauschten."""
        self.d._coord_scale = (2.0, 3.0)
        self.d._coord_offset = (100, 50)
        self.assertEqual(self.d._to_image_space(120, 110), (10, 20))
        for punkt in ((0, 0), (10, 20), (640, 360)):
            with self.subTest(punkt=punkt):
                self.assertEqual(self.d._to_image_space(*self.d._to_click_space(*punkt)), punkt)


class EveryRuntimeIsToldTheSizeTests(unittest.TestCase):
    """Die Angabe existierte und ging auf jedem Weg verloren. Drei Laufzeiten —
    an diesem Wochenende ist schon dreimal eine davon vergessen worden."""

    def test_the_voice_front_says_it(self):
        from app.services.realtime_voice_session import _bildschirm_hinweis
        hinweis = _bildschirm_hinweis({
            "screenshot_b64": "abc", "image_size": {"w": 1280, "h": 720},
            "display": 2, "displays": BILDSCHIRME,
        })
        self.assertRegex(hinweis, r"1280\D+720")
        self.assertIn("oben links", hinweis)
        self.assertIn("2 Bildschirme", hinweis)
        self.assertIn("1 (Haupt): 1440x900", hinweis)
        self.assertIn("2: 1920x1080", hinweis)
        self.assertIn("Nummer 2", hinweis)
        self.assertIn("display=2", hinweis)
        # Eine aeltere Bridge liefert nichts davon — dann kein erfundener Hinweis.
        self.assertEqual(_bildschirm_hinweis({"screenshot_b64": "abc"}), "")

    def test_the_mcp_runtime_says_it(self):
        self.assertIn("result.image_size", MCP)
        self.assertIn("result.displays", MCP)

    def test_the_custom_llm_runtime_says_it(self):
        """Nur die Form: dass die Angaben im Screenshot-Zweig gelesen werden.
        Das VERHALTEN (Groesse und Liste landen in der ``note``, die Liste nur
        bei mehr als einem Monitor) prueft ``agent/tests/test_computer_use_tool.py``
        am echten ``computer_use``-Aufruf — ein Blocktext saehe nicht, ob der
        Hinweis danach ueberhaupt ausgeliefert wird (Gegenleser-Fund, Batch 10)."""
        block = _if_block(CUSTOM, 'action == "screenshot"')
        self.assertIn('payload.get("image_size")', block)
        self.assertIn('payload.get("displays")', block)

    def test_all_three_explain_where_the_origin_is(self):
        """„1280 breit" allein hilft nicht, wenn unklar ist, wo (0,0) liegt."""
        from app.services.realtime_voice_session import _bildschirm_hinweis
        self.assertIn("oben links", _bildschirm_hinweis({"image_size": {"w": 1280, "h": 720}}))
        self.assertIn("top left", MCP)
        # Der Satz gehoert in den Groessen-Zweig — nicht irgendwo in den
        # Screenshot-Block (etwa nur fuer Mehr-Monitor-Nutzer).
        self.assertIn("top left", _if_block(CUSTOM, 'groesse.get("w")'))

    def test_the_screen_list_only_appears_with_more_than_one(self):
        """Bei einem einzigen Monitor waere der Hinweis nur Rauschen im
        Kontext."""
        for name, quelle, marke in (
            ("voice", VOICE, "len(bildschirme) > 1"),
            ("mcp", MCP, "monitore.length > 1"),
            ("custom", CUSTOM, "len(monitore) > 1"),
        ):
            with self.subTest(laufzeit=name):
                self.assertIn(marke, quelle)

    def test_the_voice_front_stays_quiet_about_a_single_screen(self):
        """Dasselbe als Ablauf fuer die Stimme: ein Monitor, keine Liste."""
        from app.services.realtime_voice_session import _bildschirm_hinweis
        hinweis = _bildschirm_hinweis({
            "image_size": {"w": 1280, "h": 800}, "display": 1, "displays": BILDSCHIRME[:1],
        })
        self.assertRegex(hinweis, r"1280\D+800")
        self.assertNotIn("Bildschirme", hinweis)
        self.assertNotIn("display=", hinweis)


class TheScreenCanBeChosenEverywhereTests(unittest.TestCase):
    def test_the_voice_tool_takes_a_display(self):
        # Das Schema, das das Modell WIRKLICH bekommt — nicht ein Ausschnitt
        # des Quelltexts: die Beschreibung ist schon zweimal gewachsen und hat
        # eine feste Fensterlaenge jedes Mal unterlaufen.
        from app.services.realtime_voice_session import DESKTOP_TOOL
        schema = json.loads(DESKTOP_TOOL["toolSpec"]["inputSchema"]["json"])
        self.assertIn("display", schema["properties"])
        self.assertEqual(schema["properties"]["display"]["type"], "number")
        self.assertIn("Hauptbildschirm", schema["properties"]["display"]["description"])

    def test_the_voice_path_forwards_it(self):
        self.assertIn('params["display"] = int(display)', VOICE)

    def test_the_mcp_tool_takes_a_display(self):
        self.assertIn("screenshotParams.display", MCP)

    def test_the_custom_llm_docs_mention_it(self):
        from pathlib import Path
        defs = (WURZEL / "agent/app/tools/definitions.py").read_text()
        self.assertIn("display: 2", defs)


if __name__ == "__main__":
    unittest.main()
