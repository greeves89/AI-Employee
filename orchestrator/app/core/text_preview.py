"""Kuerzen von Text fuer Vorschauen, ohne mitten im Wort abzuschneiden.

Anlass: die Fertigmeldung eines delegierten Auftrags wurde mit einem blossen
``text[:n]`` gekuerzt. Ein Agent beschreibt vor seiner eigentlichen Antwort erst
pflichtgemaess seine Vorabchecks (Tools laden, TODOs, Brain/Memory, Skill-Suche)
— dieser Vorspann ist oft laenger als das Limit, und die eigentliche Antwort fiel
dadurch komplett aus dem sichtbaren Text. Der Team-Lead berichtete dann, das
Ergebnis fehle, obwohl es im Original nur ausserhalb des Fensters lag.
"""


def truncate_preserving_words(text: str, limit: int) -> str:
    """Cut ``text`` to at most ``limit`` characters at the last word boundary.

    Cutting back to the last whitespace keeps the visible text readable and
    marks explicitly that something was left out, instead of ending mid-word
    the way a plain slice does.
    """
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    if cut <= 0:
        cut = limit
    return text[:cut].rstrip() + " […]"
