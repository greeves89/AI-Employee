"""Ausweichmodelle, wenn das gewählte nicht liefert (#200).

Der inhaltsbewusste Router (``orchestrator/app/core/model_router.py``) wählt das
passende Modell **vor** dem Lauf. Was fehlte, war der Fall danach: das Modell
antwortet nicht. Rate-Limit, Zeitüberschreitung, Überlastung beim Anbieter,
Wartungsfenster einer Azure-Bereitstellung — dann brach der Lauf ab, und der
Auftrag stand auf „error", obwohl ein anderes Modell ihn hätte lösen können.

**Warum die Unterscheidung retryable/nicht wichtig ist:** Bei einem falschen
Schlüssel oder einem nicht existierenden Bereitstellungsnamen hilft kein zweites
Modell — es scheitert genauso, nur langsamer und teurer. Schlimmer: die Kette
verdeckt dann die eigentliche Ursache. Ein Konfigurationsfehler soll laut und
sofort scheitern, ein Kapazitätsproblem soll ausweichen.

Alles hier ist rein: gleiche Eingabe, gleiche Ausgabe. Die Verdrahtung sitzt in
``llm_runner`` und ``llm_chat_handler`` — in **beiden**, sonst hat der Chat die
Ausfallsicherheit nicht, die der Auftrag hat.
"""

from __future__ import annotations

#: Anzeichen dafür, dass es am Modell oder an der Kapazität liegt — hier lohnt
#: der Wechsel. Bewusst als Textmuster: die Anbieter melden das über sehr
#: verschiedene Statuscodes und Rumpfformate, der Wortlaut ist der gemeinsame
#: Nenner. Kleinschreibung, Vergleich als Teilzeichenkette.
RETRYABLE_MARKERS: tuple[str, ...] = (
    "rate limit", "rate_limit", "429",
    "timeout", "timed out", "deadline exceeded",
    "overloaded", "capacity", "server_error", "service unavailable",
    "500", "502", "503", "504",
    "temporarily unavailable", "try again",
    "model_not_available", "currently unavailable",
)

#: Die Verbindung ist mittendrin abgerissen. Das ist etwas anderes als „das
#: Modell antwortet nicht": das Modell ist in Ordnung, die Leitung war es nicht.
#: Ein Modellwechsel hilft hier nichts — derselbe Aufruf noch einmal schon.
#:
#: Anlass: Bei einem Kunden scheiterten am 2026-08-13 drei Aufgaben an
#: ``ReadError('')``. Der Abbruch traf jedes Mal die Antwort des Modells, immer
#: kurz nachdem ein groesserer Stapel Werkzeug-Ergebnisse zurueckging. Keiner der
#: Marker oben passte, also galt der Fehler als endgueltig — und eine Aufgabe,
#: die 40 Zuege gelaufen war, starb an einem einzigen abgerissenen Lesevorgang.
#:
#: ``httpx`` verpackt den Socket-Fehler; der eigentliche Grund steht in der
#: Ursachenkette, die ``describe_failure`` mitliefert. Deshalb stehen hier beide
#: Ebenen — die Huelle und der Kern.
CONNECTION_MARKERS: tuple[str, ...] = (
    "readerror", "read error",
    "connecterror", "connection error",
    "connectionreset", "connection reset",
    "remotedisconnected", "server disconnected", "disconnected without response",
    "incompleteread", "incomplete read", "endofstream", "end of stream",
    "connection aborted", "connection closed", "broken pipe",
    "sslerror", "ssleoferror", "eof occurred",
    "protocolerror", "peer closed",
)

#: Anzeichen für einen Einrichtungsfehler. Die schlagen VOR den obigen zu: eine
#: Meldung wie „401 Unauthorized, try again later" darf nicht als Kapazitäts-
#: problem durchgehen, sonst probiert die Kette alle Modelle mit demselben
#: kaputten Schlüssel durch.
FATAL_MARKERS: tuple[str, ...] = (
    "unauthorized", "401", "403", "forbidden",
    "invalid api key", "invalid_api_key", "authentication",
    "deploymentnotfound", "deployment does not exist",
    "content filter", "content_filter",
)


def parse_chain(raw: str | None) -> list[str]:
    """``"a, b ,,c"`` → ``["a", "b", "c"]`` — Reihenfolge bleibt, Dubletten raus."""
    seen: list[str] = []
    for part in (raw or "").split(","):
        name = part.strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def is_connection_glitch(error_text: str | None) -> bool:
    """Ist die Verbindung abgerissen — lohnt also derselbe Aufruf noch einmal?

    Bewusst getrennt von ``is_retryable``: dort geht es um „dieses Modell kann
    gerade nicht", hier um „die Leitung war weg". Der Unterschied entscheidet, ob
    man das Modell wechselt oder schlicht noch einmal fragt. Ein
    Einrichtungsfehler bleibt auch hier endgueltig.
    """
    text = (error_text or "").lower()
    if not text:
        return False
    if any(m in text for m in FATAL_MARKERS):
        return False
    return any(m in text for m in CONNECTION_MARKERS)


def is_retryable(error_text: str | None) -> bool:
    """Lohnt sich ein anderes Modell für diesen Fehler?

    Im Zweifel **nein**. Ein unbekannter Fehler wird nicht durch Wiederholen
    besser; ihn stillschweigend auf drei Modellen zu wiederholen kostet nur Geld
    und verschleiert, was eigentlich kaputt ist.
    """
    text = (error_text or "").lower()
    if not text:
        return False
    if any(m in text for m in FATAL_MARKERS):
        return False
    return any(m in text for m in RETRYABLE_MARKERS)


def next_model(current: str | None, chain: list[str], tried: set[str] | None = None) -> str | None:
    """Das nächste noch nicht versuchte Modell aus der Kette, sonst ``None``.

    Das aktuelle Modell und alles bereits Versuchte werden übersprungen — sonst
    dreht sich die Kette im Kreis, wenn jemand das Hauptmodell auch in die
    Ausweichliste geschrieben hat.
    """
    done = {m for m in (tried or set()) if m}
    if current:
        done.add(current)
    for name in chain:
        if name not in done:
            return name
    return None
