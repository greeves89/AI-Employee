"""Wie ein Agent aussieht und wo er einsortiert ist (#523, #524).

Symbol, Farbe und Schlagwort sind **kosmetisch**: sie liegen in ``config``, lösen
kein Neuerstellen des Containers aus und haben keine Wirkung auf das Verhalten.
Deshalb stehen sie zusammen — und deshalb bedient sie EIN Endpunkt statt drei.

Bis 1.169 war die Auswahl auf 18 Symbole und 9 Farben festgenagelt, und zwar
doppelt: einmal im Frontend, einmal als Sperrliste hier. Wer ein anderes Symbol
wollte, kam auch über die Schnittstelle nicht daran vorbei. Das war als Schutz
gedacht — der Name wird im Browser auf eine Komponente abgebildet, und dort darf
nichts Beliebiges landen.

Der Schutz bleibt, die Enge fällt weg: geprüft wird jetzt die **Form** statt einer
Liste. Ein Symbolname darf nur ein Bezeichner sein, eine Farbe nur ein Farbwert.
Damit ist ausgeschlossen, was der Schutz verhindern sollte (eingeschleuste
Formatierung, Anführungszeichen, Semikolons), ohne die Auswahl zu beschneiden.

Ein unbekannter Symbolname ist **kein Fehler**: der Browser fällt auf ein
Standardsymbol zurück. Namen zu sperren, die lucide erst in der nächsten Version
kennt, wäre eine Sperre gegen die eigene Zukunft.
"""

import re

# Ein lucide-Name in PascalCase (``MessageSquare``) oder Bindestrichform
# (``message-square``) — beides kommt vor, je nachdem ob statisch importiert oder
# nachgeladen. Keine Punkte, keine Klammern, kein Leerzeichen: alles, womit man aus
# einem Namen etwas anderes machen könnte.
_ICON_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")

# Entweder ein Name aus der Palette (die alten Werte bleiben gültig) oder ein
# Farbwert. Sonst nichts — eine Farbe landet als Stilangabe im Browser.
#
# Bewusst nur die sechsstellige Form: der Browser hängt für die Hinterlegung eine
# Deckung an (``#4f46e51a``), und aus der Kurzform ``#abc`` würde dabei ``#abc1a``
# — fünf Stellen, ungültig, und der Kasten bliebe farblos. Die Farbauswahl im
# Browser liefert ohnehin immer sechs Stellen.
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

PALETTE = (
    "violet", "blue", "emerald", "amber", "rose",
    "cyan", "fuchsia", "slate", "orange",
)

TAG_MAX = 32
# Ein Schlagwort ist die Sprache des Betreibers („Kunde X", „Vertrieb", „Sandkasten")
# — es gibt bewusst KEINE Liste erlaubter Werte. Verboten sind nur Zeichen, die in
# einer Filterleiste oder einem Protokolleintrag Ärger machen.
_TAG_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f<>]")


def validate_icon(value: str) -> str:
    """Symbolname prüfen. Leer heisst „kein eigenes Symbol"."""
    value = (value or "").strip()
    if not value:
        return ""
    if not _ICON_RE.match(value):
        raise ValueError("Symbolname darf nur Buchstaben, Ziffern und Bindestriche enthalten")
    return value


def validate_color(value: str) -> str:
    """Farbe prüfen: Palettenname oder Farbwert. Leer heisst „keine eigene Farbe"."""
    value = (value or "").strip()
    if not value:
        return ""
    if value in PALETTE:
        return value
    if _HEX_RE.match(value):
        return value.lower()
    raise ValueError("Farbe muss ein Palettenname oder ein Farbwert wie #4f46e5 sein")


def validate_tag(value: str) -> str:
    """Schlagwort prüfen. Leer heisst „kein Schlagwort" und entfernt ein bestehendes."""
    value = " ".join((value or "").split())
    if not value:
        return ""
    if _TAG_FORBIDDEN.search(value):
        raise ValueError("Schlagwort enthält unerlaubte Zeichen")
    if len(value) > TAG_MAX:
        raise ValueError(f"Schlagwort ist länger als {TAG_MAX} Zeichen")
    return value


def apply_appearance(
    config: dict | None,
    *,
    icon: str | None = None,
    color: str | None = None,
    tag: str | None = None,
) -> dict:
    """Die geänderten Felder in ``config`` einarbeiten und die neue Fassung liefern.

    ``None`` heisst „nicht angefasst", ein leerer Text heisst „entfernen". Ohne
    diesen Unterschied könnte man ein einmal gesetztes Schlagwort nie wieder los
    werden, ohne den ganzen Agenten anzufassen.
    """
    result = dict(config or {})
    avatar = dict(result.get("avatar") or {})

    if icon is not None:
        avatar["icon"] = validate_icon(icon)
    if color is not None:
        avatar["color"] = validate_color(color)
    result["avatar"] = avatar

    if tag is not None:
        cleaned = validate_tag(tag)
        if cleaned:
            result["tag"] = cleaned
        else:
            result.pop("tag", None)
    return result


def tag_of(config: dict | None) -> str:
    """Das Schlagwort eines Agenten, oder ein leerer Text."""
    return str((config or {}).get("tag") or "")
