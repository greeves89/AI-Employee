"""Werkzeug-Schemas in der Form, die OpenAI/Azure annehmen (Custom-LLM-Laufzeit).

Ein MCP-Server darf sein ``inputSchema`` frei als JSON-Schema formulieren. Die
Function-Calling-Schnittstelle von OpenAI/Azure verlangt dagegen oben ein Objekt
— ``type: object`` mit ``properties`` und OHNE ``oneOf``/``anyOf``/``allOf``/
``enum``/``not`` auf der obersten Ebene. Schon EIN Werkzeug, das dagegen
verstösst, laesst die GANZE Anfrage mit 400 scheitern: Der Agent war fuer jede
Nachricht tot, weil ein einziger MCP-Server Varianten oben anbot.

Zwei Sicherungen:

* ``parameter_fuer_openai`` bringt ein Schema vor dem Senden in Form. Varianten
  oben werden zu EINEM Objekt zusammengefuehrt: Eigenschaften vereinigt (wo
  dieselbe Eigenschaft je Variante anders aussieht, als verschachteltes
  ``anyOf`` — das ist erlaubt), Pflicht ist nur, was JEDE Variante verlangt
  (bei ``allOf``: was IRGENDEINE verlangt). Die Argumente bleiben flach — der
  MCP-Server bekommt dieselbe Form wie bisher, nichts muss ausgepackt werden.
  Was sich so nicht reparieren laesst, liefert ``None``: das Werkzeug wird
  einzeln weggelassen.
* ``abgelehntes_werkzeug_streichen``: Lehnt der Anbieter trotzdem ein Werkzeug
  ab (eine Regel, die hier niemand kannte), wird genau dieses aus dem Katalog
  gestrichen und der Zug wiederholt — statt den Lauf abzubrechen.
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

#: Was OpenAI auf der obersten Ebene der Parameter nicht annimmt.
_OBEN_VERBOTEN = ("oneOf", "anyOf", "allOf", "enum", "not")
#: Was oben stehen bleiben darf. ``$defs``/``definitions`` bleiben, sonst zeigen
#: verschachtelte ``$ref`` ins Leere (auch das ist ein 400).
_OBEN_ERLAUBT = {
    "type", "properties", "required", "additionalProperties", "description",
    "title", "$defs", "definitions",
}


def _leeres_objekt() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": True}


def _aufloesen(zweig: Any, defs: dict) -> Any:
    """Einen lokalen ``$ref`` (``#/$defs/X``, ``#/definitions/X``) eine Stufe aufloesen."""
    if isinstance(zweig, dict) and isinstance(zweig.get("$ref"), str):
        m = re.fullmatch(r"#/(\$defs|definitions)/(.+)", zweig["$ref"])
        if m and isinstance(defs.get(m.group(2)), dict):
            rest = {k: v for k, v in zweig.items() if k != "$ref"}
            return {**defs[m.group(2)], **rest}
    return zweig


def _ist_objekt(zweig: Any) -> bool:
    if not isinstance(zweig, dict):
        return False
    typ = zweig.get("type")
    if typ is None:
        # Ohne Typangabe zaehlt ein Zweig als Objekt, wenn er wie eines aussieht
        # (oder gar nichts einschraenkt).
        return "items" not in zweig and "const" not in zweig and "enum" not in zweig
    if isinstance(typ, list):
        return "object" in typ
    return typ == "object"


def _eigenschaft_vereinigen(alt: Any, neu: Any) -> Any:
    if alt == neu:
        return alt
    varianten = list(alt["anyOf"]) if isinstance(alt, dict) and set(alt) == {"anyOf"} else [alt]
    if neu not in varianten:
        varianten.append(neu)
    return {"anyOf": varianten}


def parameter_fuer_openai(schema: Any) -> dict[str, Any] | None:
    """Parameter-Schema eines Werkzeugs in OpenAI-taugliche Form bringen.

    ``None`` heisst: nicht reparierbar — das Werkzeug weglassen.
    """
    if schema is None or schema == {}:
        return _leeres_objekt()
    if not isinstance(schema, dict):
        return _leeres_objekt()

    typ = schema.get("type")
    if typ is not None and not (typ == "object" or (isinstance(typ, list) and "object" in typ)):
        # Oben ein Array oder ein Text: das sind keine Funktionsargumente.
        return None

    if not any(k in schema for k in _OBEN_VERBOTEN) and typ == "object" \
            and isinstance(schema.get("properties", {}), dict) \
            and all(k in _OBEN_ERLAUBT for k in schema):
        return schema   # schon in Ordnung — unveraendert durchreichen

    defs: dict = {}
    for schluessel in ("$defs", "definitions"):
        if isinstance(schema.get(schluessel), dict):
            defs.update(schema[schluessel])

    aus: dict[str, Any] = {k: copy.deepcopy(v) for k, v in schema.items() if k in _OBEN_ERLAUBT}
    aus["type"] = "object"
    eigenschaften: dict[str, Any] = dict(aus.get("properties") or {}) \
        if isinstance(aus.get("properties"), dict) else {}
    pflicht: list[str] = [p for p in (aus.get("required") or []) if isinstance(p, str)] \
        if isinstance(aus.get("required"), list) else []

    def _zweige(schluessel: str) -> list[dict] | None:
        roh = schema.get(schluessel)
        if roh is None:
            return []
        if not isinstance(roh, list) or not roh:
            return None
        zweige = [_aufloesen(z, defs) for z in roh]
        if not all(_ist_objekt(z) for z in zweige):
            return None
        return zweige

    # allOf: alles gilt zugleich — Eigenschaften und Pflichtfelder vereinigen.
    alle = _zweige("allOf")
    if alle is None:
        return None
    for zweig in alle:
        for name, wert in (zweig.get("properties") or {}).items():
            eigenschaften[name] = _eigenschaft_vereinigen(eigenschaften[name], wert) \
                if name in eigenschaften else wert
        pflicht += [p for p in (zweig.get("required") or []) if isinstance(p, str)]

    # oneOf/anyOf: eine der Varianten — Eigenschaften vereinigen, Pflicht nur,
    # was jede Variante verlangt.
    for schluessel in ("oneOf", "anyOf"):
        varianten = _zweige(schluessel)
        if varianten is None:
            return None
        if not varianten:
            continue
        gemeinsam: set[str] | None = None
        for zweig in varianten:
            for name, wert in (zweig.get("properties") or {}).items():
                eigenschaften[name] = _eigenschaft_vereinigen(eigenschaften[name], wert) \
                    if name in eigenschaften else wert
            felder = {p for p in (zweig.get("required") or []) if isinstance(p, str)}
            gemeinsam = felder if gemeinsam is None else gemeinsam & felder
        pflicht += sorted(gemeinsam or ())

    aus["properties"] = eigenschaften
    eindeutig = list(dict.fromkeys(pflicht))
    if eindeutig:
        aus["required"] = eindeutig
    else:
        aus.pop("required", None)
    return aus


#: „Invalid schema for function 'NAME': …" — OpenAI und Azure.
_ABGELEHNT = re.compile(r"""Invalid schema for function\s+\\?['"]([^'"\\]+)\\?['"]""", re.I)


def abgelehntes_werkzeug(fehlertext: str | None) -> str | None:
    """Welches Werkzeug hat der Anbieter wegen seines Schemas abgelehnt?"""
    m = _ABGELEHNT.search(fehlertext or "")
    return m.group(1) if m else None


def abgelehntes_werkzeug_streichen(katalog: list[dict] | None, aktiviert: list[str] | None,
                                   fehlertext: str | None) -> str | None:
    """Das abgelehnte Werkzeug aus Katalog und Aktivliste nehmen.

    Liefert seinen Namen, wenn es gestrichen wurde (dann den Zug wiederholen),
    sonst ``None``. Jedes Werkzeug kann nur einmal gestrichen werden — eine
    Schleife ist damit ausgeschlossen.
    """
    name = abgelehntes_werkzeug(fehlertext)
    if not name or not katalog:
        return None
    vorher = len(katalog)
    katalog[:] = [t for t in katalog if (t.get("function") or {}).get("name") != name]
    if len(katalog) == vorher:
        return None
    if aktiviert is not None and name in aktiviert:
        aktiviert.remove(name)
    logger.warning(
        "Werkzeug %s vom Anbieter wegen seines Schemas abgelehnt — fuer diese "
        "Sitzung weggelassen, der Lauf geht ohne es weiter: %s",
        name, (fehlertext or "")[:400],
    )
    return name
