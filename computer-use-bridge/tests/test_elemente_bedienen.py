"""Elemente selbst bedienen — Windows UI Automation und macOS AX, ohne Koordinate.

Bis hierher konnte die Bridge Elemente nur FINDEN und dann auf ihre Mitte
klicken. Unter Windows geht das bei Skalierung, mehreren Monitoren oder
verdeckten Fenstern daneben; auf dem Mac war der Baum sogar immer leer
(``elem.AXRole`` gibt es in pyobjc nicht). Jetzt loest die Bridge das Element
ueber seine eigenen Muster aus und faellt nur ohne solche auf Klick/Tippen zurueck.

Die Plattform-Logik laeuft hier gegen nachgebaute Elemente, damit sie auf jedem
System prueft. Der Test ganz unten bedient unter Windows ein echtes Notepad —
er laeuft im Windows-Build (build-bridge.yml) und sonst nicht.
"""

import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge

# ── Nachgebaute Elemente ──────────────────────────────────────────────────────

class Knoten:
    """Ein Element fuer die plattformneutrale Suche: Merkmale + Kinder."""

    def __init__(self, role="", title="", bbox=True, kinder=(), value=""):
        self.m = {"role": role}
        if title:
            self.m["title"] = title
        if value:
            self.m["value"] = value
        if bbox:
            self.m["bbox"] = {"x": 10, "y": 20, "w": 100, "h": 30}
        self.kinder = list(kinder)


def _suche(wurzel, query, role="", **kw):
    return bridge._breitensuche(wurzel, lambda k: k.kinder, lambda k: k.m, query, role, **kw)


class Muster:
    """Ein UI-Automation-Muster, das mitschreibt, was aufgerufen wurde."""

    def __init__(self, protokoll, **stand):
        self.protokoll = protokoll
        self.__dict__.update(stand)

    def Invoke(self):
        self.protokoll.append("Invoke")

    def Toggle(self):
        self.protokoll.append("Toggle")
        self.ToggleState = 0 if self.ToggleState == 1 else 1

    def Select(self):
        self.protokoll.append("Select")
        self.IsSelected = True

    def Expand(self):
        self.protokoll.append("Expand")
        self.ExpandCollapseState = 1

    def Collapse(self):
        self.protokoll.append("Collapse")
        self.ExpandCollapseState = 0

    def SetValue(self, wert):
        self.protokoll.append(f"SetValue:{wert}")
        self.Value = wert


class WinCtrl:
    """Ein Windows-Steuerelement mit ausgewaehlten Mustern."""

    def __init__(self, name="", typ="ButtonControl", muster=None, kinder=(), enabled=True):
        self.Name = name
        self.ControlTypeName = typ
        self.HelpText = ""
        self.AutomationId = ""
        self.IsEnabled = enabled
        self.IsPassword = False
        self.protokoll: list[str] = []
        self._muster = {k: Muster(self.protokoll, **v) for k, v in (muster or {}).items()}
        self._kinder = list(kinder)

        class R:
            left, top, right, bottom = 0, 0, 80, 20

        self.BoundingRectangle = R()

    def GetChildren(self):
        return self._kinder

    def SetFocus(self):
        self.protokoll.append("SetFocus")

    def __getattr__(self, name):
        if name.startswith("Get") and name.endswith("Pattern"):
            muster = self.__dict__["_muster"].get(name[3:-7])
            return lambda: muster
        raise AttributeError(name)


# ── Suche ─────────────────────────────────────────────────────────────────────

class Suche(unittest.TestCase):
    def test_genaue_beschriftung_schlaegt_teiltreffer(self):
        """„Speichern" trifft den Knopf „Speichern", nicht „Speichern unter …"."""
        unter = Knoten("AXMenuItem", "Speichern unter …")
        genau = Knoten("AXButton", "Speichern")
        el, _ = _suche(Knoten("AXWindow", kinder=[unter, Knoten("AXGroup", kinder=[genau])]),
                       "speichern")
        self.assertIs(el, genau)

    def test_ohne_genauen_treffer_der_erste_teiltreffer(self):
        a, b = Knoten("AXButton", "Datei öffnen"), Knoten("AXButton", "Datei schließen")
        el, _ = _suche(Knoten(kinder=[a, b]), "datei")
        self.assertIs(el, a)

    def test_breite_vor_tiefe(self):
        """Der Knopf im Fenster kommt vor einem gleichnamigen Eintrag tief in einer Liste."""
        tief = Knoten("AXStaticText", "OK")
        oben = Knoten("AXButton", "OK")
        el, _ = _suche(Knoten(kinder=[Knoten(kinder=[Knoten(kinder=[tief])]), oben]), "OK")
        self.assertIs(el, oben)

    def test_unsichtbares_zaehlt_nicht(self):
        el, _ = _suche(Knoten(kinder=[Knoten("AXButton", "OK", bbox=False)]), "OK")
        self.assertIsNone(el)

    def test_rolle_plattformneutral_und_genau_bevorzugt(self):
        radio = Knoten("RadioButtonControl", "A")
        knopf = Knoten("ButtonControl", "B")
        el, _ = _suche(Knoten(kinder=[radio, knopf]), "", "button")
        self.assertIs(el, knopf)

    def test_suche_im_wert(self):
        feld = Knoten("EditControl", value="Max Mustermann")
        el, _ = _suche(Knoten(kinder=[feld]), "mustermann")
        self.assertIs(el, feld)

    def test_deckel_gegen_riesige_baeume(self):
        breit = Knoten(kinder=[Knoten("AXStaticText", f"x{i}") for i in range(500)]
                       + [Knoten("AXButton", "Ziel")])
        el, _ = _suche(breit, "ziel", max_knoten=100)
        self.assertIsNone(el)


# ── Windows-Muster ────────────────────────────────────────────────────────────

class WindowsMuster(unittest.TestCase):
    def test_press_loest_invoke_aus(self):
        c = WinCtrl("OK", muster={"Invoke": {}})
        self.assertEqual(bridge._win_ausfuehren(None, c, "press")["methode"], "InvokePattern")
        self.assertEqual(c.protokoll, ["Invoke"])

    def test_press_ohne_muster_faellt_auf_klick_zurueck(self):
        r = bridge._win_ausfuehren(None, WinCtrl("Bild", "ImageControl"), "press")
        self.assertFalse(r["ok"])
        self.assertEqual(r["fallback"], "click")

    def test_toggle_auf_zielstand(self):
        c = WinCtrl("Merken", "CheckBoxControl", muster={"Toggle": {"ToggleState": 0}})
        bridge._win_ausfuehren(None, c, "toggle", "true")
        bridge._win_ausfuehren(None, c, "toggle", "true")  # schon an: nichts tun
        self.assertEqual(c.protokoll, ["Toggle"])
        self.assertTrue(bridge._win_zustand(c)["checked"])

    def test_wert_setzen(self):
        c = WinCtrl("Name", "EditControl", muster={"Value": {"Value": "", "IsReadOnly": False}})
        r = bridge._win_ausfuehren(None, c, "set_value", "Müller & Söhne")
        self.assertTrue(r["ok"])
        self.assertEqual(bridge._win_zustand(c)["value"], "Müller & Söhne")

    def test_schreibgeschuetztes_feld_wird_getippt(self):
        c = WinCtrl("Name", "EditControl", muster={"Value": {"Value": "", "IsReadOnly": True}})
        self.assertEqual(bridge._win_ausfuehren(None, c, "set_value", "x")["fallback"], "type")

    def test_passwortfeld_wird_nie_ausgelesen(self):
        c = WinCtrl("Kennwort", "EditControl", muster={"Value": {"Value": "geheim"}})
        c.IsPassword = True
        self.assertNotIn("value", bridge._win_merkmale(c))
        self.assertNotIn("value", bridge._win_zustand(c))

    def test_auswahlliste_aufklappen_waehlen_zuklappen(self):
        eintrag = WinCtrl("Berlin", "ListItemControl", muster={"SelectionItem": {"IsSelected": False}})
        liste = WinCtrl("Ort", "ComboBoxControl",
                        muster={"ExpandCollapse": {"ExpandCollapseState": 0}},
                        kinder=[WinCtrl("Hamburg", "ListItemControl"), eintrag])
        r = bridge._win_ausfuehren(None, liste, "select", "Berlin")
        self.assertTrue(r["ok"])
        self.assertEqual(eintrag.protokoll, ["Select"])
        self.assertEqual(liste.protokoll, ["Expand", "Collapse"])

    def test_unbekannter_eintrag_wird_gemeldet(self):
        liste = WinCtrl("Ort", "ComboBoxControl", kinder=[WinCtrl("Hamburg", "ListItemControl")])
        r = bridge._win_ausfuehren(None, liste, "select", "Berlin")
        self.assertFalse(r["ok"])
        self.assertIn("Berlin", r["error"])

    def test_aufklappen(self):
        c = WinCtrl("Ordner", "TreeItemControl", muster={"ExpandCollapse": {"ExpandCollapseState": 0}})
        bridge._win_ausfuehren(None, c, "expand")
        self.assertTrue(bridge._win_zustand(c)["expanded"])


# ── Ablauf im Dispatcher ──────────────────────────────────────────────────────

class FakeCtrl:
    def __init__(self):
        self.protokoll = []

    def _require_input_permission(self):
        self.protokoll.append("freigabe")

    def click(self, x, y, button="left", double=False):
        self.protokoll.append(("click", x, y))

    def key_press(self, keys):
        self.protokoll.append(("keys", tuple(keys)))

    def type_text(self, text, interval=0.02):
        self.protokoll.append(("type", text))


def _dispatcher():
    d = object.__new__(bridge.CommandDispatcher)
    d._ctrl = FakeCtrl()
    d._coord_scale = (1.0, 1.0)
    d._coord_offset = (0, 0)
    return d


ELEMENT = {"found": True, "role": "EditControl", "title": "Name",
           "bbox": {"x": 0, "y": 0, "w": 100, "h": 20}, "center": {"x": 50, "y": 10}}


class Dispatcher(unittest.TestCase):
    def setUp(self):
        self._alt = bridge.element_bedienen

    def tearDown(self):
        bridge.element_bedienen = self._alt

    def test_direkter_weg_ohne_klick(self):
        bridge.element_bedienen = lambda *a, **k: {"ok": True, "methode": "InvokePattern",
                                                   "element": dict(ELEMENT)}
        d = _dispatcher()
        r = d.dispatch({"action": "element_press", "params": {"query": "OK"}})
        self.assertEqual(r["methode"], "InvokePattern")
        self.assertEqual(d._ctrl.protokoll, ["freigabe"])  # kein Klick, keine Taste

    def test_rueckfall_tippen_klickt_markiert_und_tippt(self):
        bridge.element_bedienen = lambda *a, **k: {"ok": False, "fallback": "type",
                                                   "grund": "x", "element": dict(ELEMENT)}
        d = _dispatcher()
        r = d.dispatch({"action": "element_set_value", "params": {"query": "Name", "value": "Max"}})
        self.assertTrue(r["ok"])
        self.assertEqual(r["methode"], "klick+tippen")
        self.assertEqual(d._ctrl.protokoll[1], ("click", 50, 10))
        self.assertEqual(d._ctrl.protokoll[-1], ("type", "Max"))

    def test_schritt_und_wert_kommen_an(self):
        gesehen = {}

        def fake(aktion, query, role, app, wert, sperrzone=None, streng=False):
            gesehen.update(aktion=aktion, query=query, app=app, wert=wert,
                           sperre=sperrzone is bridge.in_overlay)
            return {"ok": True, "methode": "TogglePattern", "element": dict(ELEMENT)}

        bridge.element_bedienen = fake
        _dispatcher().dispatch({"action": "element_press", "params": {
            "query": "Merken", "aktion": "toggle", "value": "true", "app": "Excel"}})
        self.assertEqual(gesehen, {"aktion": "toggle", "query": "Merken", "app": "Excel",
                                   "wert": "true", "sperre": True})

    def test_set_value_ohne_wert_ist_ein_fehler(self):
        r = _dispatcher().dispatch({"action": "element_set_value", "params": {"query": "Name"}})
        self.assertIn("value", r["error"])


class Gemeinsam(unittest.TestCase):
    def test_unbekannter_schritt(self):
        r = bridge.element_bedienen("fliegen", "x")
        self.assertFalse(r["ok"])
        self.assertIn("press", r["error"])

    def test_sperrzone_haelt_ab(self):
        """Das Overlay des Menschen bedient nur er selbst — auch nicht ueber UIA."""
        alt = bridge._element_mit
        try:
            bridge._element_mit = lambda app, q, r, schritt, streng=False: schritt(
                ("win", None), WinCtrl("OK", muster={"Invoke": {}}), {**ELEMENT})
            r = bridge.element_bedienen("press", "OK", sperrzone=lambda x, y: True)
        finally:
            bridge._element_mit = alt
        self.assertFalse(r["ok"])
        self.assertIn("Overlay", r["error"])

    def test_angekuendigt_nur_mit_bedienungshilfen(self):
        self.assertIn("element_press", bridge.AX_ACTIONS)
        self.assertIn("element_set_value", bridge.AX_ACTIONS)


# ── Echt unter Windows ────────────────────────────────────────────────────────

def _uia_da() -> bool:
    try:
        import uiautomation  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(sys.platform == "win32" and _uia_da(), "nur unter Windows mit uiautomation")
class EchtesNotepad(unittest.TestCase):
    """Ein echtes Programm bedienen — im Windows-Build vor jeder Auslieferung."""

    def setUp(self):
        self.proc = subprocess.Popen(["notepad.exe"])
        deadline = time.time() + 15
        while time.time() < deadline:
            if bridge.element_finden("", "edit", "notepad").get("found") or \
               bridge.element_finden("", "document", "notepad").get("found"):
                return
            time.sleep(0.5)
        self.fail("Notepad kam nicht hoch")

    def tearDown(self):
        subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True, check=False)

    def test_feld_fuellen_und_wiederfinden(self):
        rolle = "edit" if bridge.element_finden("", "edit", "notepad").get("found") else "document"
        r = bridge.element_bedienen("set_value", "", rolle, "notepad", "Hallo aus der Bridge")
        if not r.get("ok"):
            # Ohne ValuePattern meldet die Bridge den Rueckfall — das ist korrekt,
            # aber dann pruefen wir das Tippen ueber den Dispatcher.
            self.assertEqual(r.get("fallback"), "type", r)
            return
        self.assertEqual(r["zustand"].get("value"), "Hallo aus der Bridge", r)
        self.assertTrue(bridge.element_finden("Hallo aus der", "", "notepad").get("found"))

    def test_fenster_und_baum(self):
        fenster = bridge.list_windows()
        self.assertTrue(any("notepad" in w["app"].lower() for w in fenster["windows"]), fenster)
        baum = bridge.get_ax_tree("notepad", 4)
        self.assertNotIn("error", baum)
        self.assertTrue(baum.get("children"))

    def test_aus_einem_anderen_thread(self):
        """Die Bridge fuehrt Befehle im Thread-Pool aus — COM muss dort initialisiert sein."""
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(2) as pool:
            ergebnisse = list(pool.map(lambda _: bridge.get_ax_tree("notepad", 2), range(4)))
        for baum in ergebnisse:
            self.assertNotIn("error", baum)


if __name__ == "__main__":
    unittest.main()


class FreigabelisteStreng(unittest.TestCase):
    """Bei beschraenkten Sitzungen loest die Bridge Programme nur exakt auf.

    Sonst traefe „Excel" auch ein Browserfenster „Excel-Tipps – Chrome" und die
    Freigabeliste des Servers waere umgangen.
    """

    class Fenster:
        def __init__(self, name, pid):
            self.Name, self.ProcessId = name, pid

    class Auto:
        def __init__(self, fenster, prozesse):
            self._f, self._p = fenster, prozesse

        def GetRootControl(self):
            f = self._f

            class Root:
                def GetChildren(self):
                    return f

            return Root()

        def ProcessIdToProcessName(self, pid):
            return self._p[pid]

    def _auto(self):
        chrome = self.Fenster("Excel-Tipps – Google Chrome", 1)
        excel = self.Fenster("Mappe1 - Excel", 2)
        return self.Auto([chrome, excel], {1: "chrome.exe", 2: "EXCEL.EXE"}), chrome, excel

    def test_genauer_prozess_gewinnt_auch_ohne_streng(self):
        auto, _chrome, excel = self._auto()
        self.assertIs(bridge._win_wurzel(auto, "Excel"), excel)

    def test_streng_kein_titel_teiltreffer(self):
        auto, chrome, _excel = self._auto()
        auto._f = [chrome]  # Excel ist zu, nur der Browser hat „Excel" im Titel
        self.assertIs(bridge._win_wurzel(auto, "Excel"), chrome)  # unbeschraenkt: wie bisher
        with self.assertRaises(LookupError):
            bridge._win_wurzel(auto, "Excel", streng=True)

    def test_streng_ohne_app_kein_ganzer_desktop(self):
        auto, *_ = self._auto()
        with self.assertRaises(LookupError):
            bridge._win_wurzel(auto, "", streng=True)

    def test_dispatcher_reicht_streng_durch(self):
        gesehen = {}
        alt = bridge.element_bedienen
        try:
            def fake(*a, **k):
                gesehen.update(k)
                return {"ok": True, "methode": "x", "element": dict(ELEMENT)}
            bridge.element_bedienen = fake
            _dispatcher().dispatch({"action": "element_press",
                                    "params": {"query": "OK", "app": "Excel", "app_streng": True}})
        finally:
            bridge.element_bedienen = alt
        self.assertTrue(gesehen["streng"])
