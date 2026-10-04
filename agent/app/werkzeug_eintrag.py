"""Werkzeugaufrufe fuer den gespeicherten Verlauf — eine Form fuer alle Laufzeiten (#911).

Bis 1.361 legten Claude Code, der Custom-LLM-Chat und der Custom-LLM-Auftrag die
Eingabe je als ``json.dumps(eingabe)[:200]`` ab. Der Schnitt fiel mitten ins
JSON; beim Laden scheiterte ``JSON.parse``, und im Verlauf stand ``IN {}``. Die
Ausgabe eines Werkzeugs wurde gar nicht gespeichert — „Befehl ausgeführt" war
nach dem Neuladen leer.

Jetzt:

* Die Eingabe wird gekuerzt, bleibt aber GUELTIGES JSON: lange Strings werden
  einzeln gekuerzt, notfalls auch lange Listen und grosse Objekte.
* Die Ausgabe kommt gekuerzt an den Eintrag, zu dem sie gehoert (``tool_use_id``).
* Helfer (Subagenten, Delegationen) behalten ihre Kernfelder, siehe
  ``app/subagent_felder.py``.

Form eines Eintrags (so liest ihn der Orchestrator aus dem ``done``-Ereignis)::

    {"tool": "Bash", "input": "<gueltiges JSON, <= EINGABE_GRENZE Zeichen>",
     "tool_use_id": "toolu_...", "output": "<Text, <= AUSGABE_GRENZE Zeichen>",
     "subagent": {...}}          # nur bei Helfern

``output`` fehlt, solange (oder wenn nie) ein Ergebnis kam.
"""
from __future__ import annotations

import json

from app.subagent_felder import subagent_felder

#: Hoechstlaenge der gespeicherten Eingabe (Zeichen, gueltiges JSON).
EINGABE_GRENZE = 2000
#: Hoechstlaenge der gespeicherten Ausgabe (Zeichen).
AUSGABE_GRENZE = 1000
_KUERZUNG = "…"


def _dump(wert) -> str:
    return json.dumps(wert, ensure_ascii=False, default=str)


def _gekuerzt(wert, text_max: int, liste_max: int | None, objekt_max: int | None):
    """Kopie von ``wert`` mit gekuerzten Strings (und ggf. Listen/Objekten)."""
    if isinstance(wert, str):
        return wert if len(wert) <= text_max else wert[:text_max] + _KUERZUNG
    if isinstance(wert, list):
        teile = wert if liste_max is None else wert[:liste_max]
        neu = [_gekuerzt(w, text_max, liste_max, objekt_max) for w in teile]
        if len(teile) < len(wert):
            neu.append(f"{_KUERZUNG} {len(wert) - len(teile)} weitere")
        return neu
    if isinstance(wert, dict):
        paare = list(wert.items())
        teile = paare if objekt_max is None else paare[:objekt_max]
        neu = {str(k): _gekuerzt(v, text_max, liste_max, objekt_max) for k, v in teile}
        if len(teile) < len(paare):
            neu[_KUERZUNG] = f"{len(paare) - len(teile)} weitere Felder"
        return neu
    return wert


def eingabe_json(eingabe, grenze: int = EINGABE_GRENZE) -> str:
    """Die Werkzeugeingabe als gueltiges JSON von hoechstens ``grenze`` Zeichen."""
    if eingabe is None:
        return "{}"
    try:
        voll = _dump(eingabe)
    except (TypeError, ValueError):
        voll = _dump(str(eingabe))
    if len(voll) <= grenze:
        return voll
    # Erst nur die Strings kuerzen (das trifft fast immer: ein langer Befehl,
    # ein langer Auftragstext), dann auch Listen und Objekte.
    for text_max, liste_max, objekt_max in (
        (500, None, None), (200, None, None), (80, None, None),
        (80, 20, 30), (30, 10, 15), (20, 3, 5),
    ):
        kandidat = _dump(_gekuerzt(eingabe, text_max, liste_max, objekt_max))
        if len(kandidat) <= grenze:
            return kandidat
    # Letzter Ausweg: der Anfang des Originals als Text — weiterhin gueltiges JSON.
    rest = grenze - 40
    while rest > 0:
        kandidat = _dump({_KUERZUNG: voll[:rest] + _KUERZUNG})
        if len(kandidat) <= grenze:
            return kandidat
        rest -= 100
    return "{}"


def ausgabe_text(inhalt, grenze: int = AUSGABE_GRENZE) -> str:
    """Lesbarer Text eines Werkzeugergebnisses, hoechstens ``grenze`` Zeichen.

    Versteht die Formen der Laufzeiten: reiner Text, Claude-Inhaltsbloecke
    (``[{"type": "text", "text": ...}, {"type": "image", ...}]``) und Objekte.
    """
    teile: list[str] = []

    def sammle(wert) -> None:
        if isinstance(wert, str):
            teile.append(wert)
        elif isinstance(wert, list):
            for w in wert:
                sammle(w)
        elif isinstance(wert, dict):
            if isinstance(wert.get("text"), str):
                teile.append(wert["text"])
            elif wert.get("type") == "image":
                teile.append("[Bild]")
            elif "content" in wert:
                sammle(wert["content"])
            else:
                teile.append(_dump(wert))
        elif wert is not None:
            teile.append(str(wert))

    sammle(inhalt)
    text = "\n".join(t for t in teile if t)
    if len(text) <= grenze:
        return text
    return text[: grenze - len(_KUERZUNG)] + _KUERZUNG


class WerkzeugListe:
    """Die Werkzeugaufrufe eines Zuges, wie sie im Verlauf gespeichert werden."""

    def __init__(self) -> None:
        self._eintraege: list[dict] = []
        self._nach_id: dict[str, dict] = {}

    def aufruf(self, werkzeug: str, eingabe, tool_use_id: str = "") -> dict | None:
        """Einen Aufruf festhalten. ``None``, wenn dieselbe ID schon da ist."""
        if tool_use_id and tool_use_id in self._nach_id:
            return None
        eintrag: dict = {"tool": werkzeug, "input": eingabe_json(eingabe)}
        if tool_use_id:
            eintrag["tool_use_id"] = tool_use_id
            self._nach_id[tool_use_id] = eintrag
        felder = subagent_felder(werkzeug, eingabe if isinstance(eingabe, dict) else None)
        if felder:
            eintrag["subagent"] = felder
        self._eintraege.append(eintrag)
        return eintrag

    def ergebnis(self, tool_use_id: str, inhalt) -> None:
        """Die Ausgabe an den passenden Aufruf haengen (unbekannte IDs: nichts)."""
        eintrag = self._nach_id.get(tool_use_id or "")
        if eintrag is not None:
            eintrag["output"] = ausgabe_text(inhalt)

    def liste(self) -> list[dict] | None:
        return self._eintraege or None
