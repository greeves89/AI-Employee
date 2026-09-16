"""Der Fuellstand des Kontextfensters ueberlebt ein Neuladen.

Nach 1.315.2 zeigte der Ring nach einem Neuladen wieder die Schaetzung, bis
der naechste Zug lief — obwohl sich am Kontext des Agenten durch ein Neuladen
nichts aendert. Rueckmeldung: "nur weil ich eine Seite refreshe, faengt der
doch nicht neu an."

Der Server legt den letzten Aufruf (context_tokens aus dem done-Ereignis) in
der Nachrichten-Meta ab; die Oberflaeche nimmt beim Laden den juengsten Wert.
Ausdruecklich NICHT input_tokens — das ist die Summe aller Aufrufe eines Zuges.
"""

import ast
import builtins
import dis
import json
import logging
import re
import textwrap
import types
import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]
WS = (ORCH / "app" / "api" / "ws.py").read_text()
FRONT = ORCH.parent / "frontend" / "src" / "components" / "agents" / "chat.tsx"


def _process_event_double():
    """`_process_event` ist eine in `ws_agent_chat` eingebettete Funktion mit
    `nonlocal`-Zustand. Statt den ganzen WebSocket-Handler zu fahren, wird der
    Knoten per AST ausgeschnitten und in eine Huelle gesetzt, die genau den
    Zustand stellt, den die Funktion braucht. Was zurueckkommt, ist die ECHTE
    Funktion aus ws.py — kein Nachbau, keine Textsuche."""
    knoten = next(
        n for n in ast.walk(ast.parse(WS))
        if isinstance(n, ast.FunctionDef) and n.name == "_process_event"
    )
    zeilen = WS.splitlines(keepends=True)
    quelle = textwrap.dedent("".join(zeilen[knoten.lineno - 1:knoten.end_lineno]))
    huelle = (
        "def _huelle(json, logger, _auto_presented_files_from_text,\n"
        "            _auto_presented_files_from_tool_calls):\n"
        "    _streaming_responses, _seen_tool_ids, _pending_message_ids = {}, set(), set()\n"
        + textwrap.indent(quelle, "    ")
        + "    return _process_event\n"
    )
    ns: dict = {}
    exec(huelle, ns)
    verarbeite = ns["_huelle"](json, logging.getLogger("ws-test"),
                               lambda content: [], lambda tool_calls: [])
    # Ein neuer globaler Name in ws.py (z. B. `logger`) wuerde in der Huelle zu
    # einem NameError, den `except Exception: pass` in _process_event schluckt —
    # der Test wuerde rot mit einer irrefuehrenden Meldung. Deshalb laut:
    fehlt = _globale_namen(verarbeite.__code__) - set(ns) - set(dir(builtins))
    if fehlt:
        raise AssertionError(f"_process_event braucht {sorted(fehlt)} — in die Huelle aufnehmen")
    return verarbeite


def _globale_namen(code) -> set[str]:
    namen = {i.argval for i in dis.get_instructions(code) if i.opname == "LOAD_GLOBAL"}
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            namen |= _globale_namen(const)
    return namen


class ServerSpeichertTests(unittest.TestCase):
    def _done(self, **data):
        verarbeite = _process_event_double()
        ereignis = json.dumps({"type": "done", "message_id": "m1",
                               "data": {"text": "fertig", **data}})
        ergebnis = verarbeite(ereignis)
        self.assertIsNotNone(ergebnis, "done-Ereignis wurde nicht als Abschluss erkannt")
        art, mid, _resp, meta = ergebnis
        self.assertEqual((art, mid), ("done", "m1"))
        return meta

    def test_done_meta_enthaelt_context_tokens(self):
        """Der Wert des LETZTEN Aufrufs landet in der Meta — nicht die Summe."""
        meta = self._done(context_tokens=41_000, input_tokens=97_000)
        self.assertEqual(meta.get("context_tokens"), 41_000)
        self.assertNotEqual(meta.get("context_tokens"), meta.get("input_tokens"))

    def test_ohne_gemeldeten_fuellstand_bleibt_die_meta_frei(self):
        """Kein Wert ist kein Wert — die Oberflaeche soll keine Null als Stand lesen."""
        meta = self._done(input_tokens=97_000)
        self.assertNotIn("context_tokens", meta)
        meta = self._done(context_tokens=0, input_tokens=97_000)
        self.assertNotIn("context_tokens", meta)


def _ts_teile(text: str) -> list[tuple[str, str]]:
    """Den Text in (art, stueck) zerlegen: "code", "string" ('…', "…", `…`)
    oder "kommentar" (`//…`, `/*…*/`). Ein `https://` in einem String ist kein
    Kommentar; eine Klammer in einem String oder Kommentar ist keine Klammer."""
    teile: list[tuple[str, str]] = []
    code: list[str] = []
    i, n = 0, len(text)

    def _code_ab():
        if code:
            teile.append(("code", "".join(code)))
            code.clear()

    while i < n:
        c = text[i]
        if c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            _code_ab()
            teile.append(("string", text[i:j + 1]))
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            _code_ab()
            teile.append(("kommentar", text[i:j]))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            _code_ab()
            teile.append(("kommentar", text[i:j]))
            i = j
        else:
            code.append(c)
            i += 1
    _code_ab()
    return teile


def _ohne_ts_kommentare(text: str) -> str:
    """Ein auskommentierter Aufruf darf kein `assertIn` mehr bestehen (#726)."""
    return "".join(t for art, t in _ts_teile(text) if art != "kommentar")


def _ts_block(src: str, kopf: str) -> str:
    """Der Klammerblock, der mit dem letzten Zeichen von `kopf` (einer oeffnenden
    Klammer) beginnt — ohne Kommentare, Klammern nur im Code gezaehlt. Ein
    Kommentar wie `// Form: {` vor dem Block kann ihn so nicht stumm ausweiten."""
    start = src.index(kopf)
    tiefe, out = 0, []
    for art, t in _ts_teile(src[start + len(kopf) - 1:]):
        if art == "kommentar":
            continue
        if art == "string":
            out.append(t)
            continue
        for j, c in enumerate(t):
            if c in "([{":
                tiefe += 1
            elif c in ")]}":
                tiefe -= 1
                if tiefe == 0:
                    out.append(t[:j + 1])
                    return "".join(out)
        out.append(t)
    raise ValueError(f"unbalancierte Klammer hinter {kopf!r}")


def _lade_verlauf_block() -> str:
    """Der Rumpf von `loadHistory` als Klammerblock statt als 1500-Zeichen-Fenster
    hinter `setTaskCards(...)`: ein laengerer Kommentar verschiebt den Block
    nicht, und Kommentare zaehlen nicht als Aufruf."""
    return _ts_block(FRONT.read_text(), "const loadHistory = async () => {")


class OberflaecheLiestBeimLadenTests(unittest.TestCase):
    def test_letzter_gespeicherter_stand_wird_uebernommen(self):
        block = _lade_verlauf_block()
        # Der JUENGSTE Stand: von hinten suchen, nicht von vorn.
        self.assertRegex(block, re.compile(
            r"\[\.\.\.history\]\.reverse\(\)\.find\([^;]*meta\?\.context_tokens", re.S))
        self.assertIn("setLiveContextTokens(letzteMitStand?.meta?.context_tokens ?? null);", block)
        # und NICHT die Summe
        self.assertNotIn("meta?.input_tokens", block)

    def test_der_ring_faellt_nicht_mehr_stumpf_auf_null(self):
        """Weder `null` noch `0` als fester Wert — beides waere die Schaetzung
        von vorn, genau die Rueckmeldung aus dem Kopf dieser Datei."""
        block = _lade_verlauf_block()
        self.assertNotRegex(block, re.compile(r"setLiveContextTokens\((null|0)\)"))
        self.assertNotRegex(block, re.compile(r"context_tokens \?\? 0\b"))


if __name__ == "__main__":
    unittest.main()
