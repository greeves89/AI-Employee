"""Auto-Capture (#385) — was hereinkommt, landet im Second Brain statt im Chatverlauf.

Der Anlass: Man schickt dem Agenten einen Link oder einen laengeren Textblock, er
antwortet darauf, und danach ist es weg. Im Chat steht es zwar noch, aber es ist
weder auffindbar noch verknuepft — das Wissen ist praktisch verloren.

Erkannt wird dreierlei, bewusst in dieser Reihenfolge:

1. **Ausdruecklicher Auftrag** („merk dir das", „speicher das") — schlaegt alles andere.
2. **Link** — eine URL ist so gut wie immer als Ablage gemeint, nicht als Plauderei.
3. **Langer Text** (> 500 Zeichen) — jemand hat etwas hereinkopiert.

Geschrieben wird ueber ``core.knowledge_write`` — denselben Weg wie Nachtschicht,
Wochensynthese und die Wissens-API, damit der Eintrag eingebettet und verknuepft ist
und nicht als unsichtbare Karteileiche endet.

Der Eintrag traegt ``#capture`` und ``#unread``. ``#unread`` ist das, was die
Capture-Inbox filtert; wird der Eintrag behalten, faellt die Markierung weg.
"""

import logging
import re

logger = logging.getLogger(__name__)

CAPTURE_TAG = "capture"
UNREAD_TAG = "unread"

LONG_TEXT_CHARS = 500
TITLE_MAX = 70

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# Ausdrueckliche Merk-Auftraege. Bewusst knapp gehalten: je mehr Wendungen hier
# stehen, desto haeufiger wird eine normale Bitte faelschlich als Ablage gewertet.
_EXPLICIT_RE = re.compile(
    r"\b("
    r"merk(?:e)?\s+dir\s+(?:das|dies|folgendes)"
    r"|speicher(?:e|s)?\s+(?:das|dies|folgendes)"
    r"|schreib(?:e)?\s+(?:das|dies)\s+auf"
    r"|notier(?:e)?\s+(?:das|dies)"
    r"|remember\s+(?:this|that)"
    r"|save\s+this"
    r")\b",
    re.IGNORECASE,
)

# Reine Steueranweisungen sind nie Ablage — auch dann nicht, wenn sie lang sind.
_COMMAND_PREFIXES = ("/", "!")


def detect(text: str) -> str | None:
    """Warum das hier aufgehoben gehoert — oder ``None``, wenn nicht.

    Rueckgabe ist der Grund (``"explicit"`` / ``"link"`` / ``"long_text"``), damit
    der Aufrufer ihn melden und der Eintrag ihn festhalten kann. Ein blosses ``True``
    waere hier zu wenig: „warum liegt das in meiner Inbox?" ist die erste Frage,
    die ein Nutzer stellt.
    """
    if not text:
        return None
    stripped = text.strip()
    if not stripped or stripped.startswith(_COMMAND_PREFIXES):
        return None
    if _EXPLICIT_RE.search(stripped):
        return "explicit"
    if _URL_RE.search(stripped):
        return "link"
    if len(stripped) > LONG_TEXT_CHARS:
        return "long_text"
    return None


def build_title(text: str, reason: str) -> str:
    """Eine Ueberschrift, an der man den Eintrag spaeter wiedererkennt.

    Bei einem Link die Adresse (gekuerzt), sonst der erste Satz. Der Titel ist der
    Schluessel fuer das Anlegen/Ergaenzen — zweimal derselbe Link ergaenzt also den
    bestehenden Eintrag, statt einen zweiten anzulegen.
    """
    stripped = (text or "").strip()
    if reason == "link":
        match = _URL_RE.search(stripped)
        if match:
            url = match.group(0).rstrip(".,;:)")
            return url[:TITLE_MAX]
    first_line = next((ln.strip() for ln in stripped.splitlines() if ln.strip()), "")
    sentence = re.split(r"(?<=[.!?])\s", first_line)[0] if first_line else ""
    title = (sentence or first_line or stripped)[:TITLE_MAX].strip()
    return title or "Notiz"


REASON_LABEL = {
    "explicit": "ausdrücklich gemerkt",
    "link": "Link",
    "long_text": "langer Text",
}


def build_content(text: str, reason: str, source: str) -> str:
    label = REASON_LABEL.get(reason, reason)
    return f"{(text or '').strip()}\n\n---\nAufgenommen aus {source} ({label})."


async def capture(db, *, user_id: str | None, text: str, source: str,
                  author: str) -> tuple[object | None, str | None]:
    """Text pruefen und bei Bedarf als Wissenseintrag ablegen.

    Gibt ``(Eintrag, Grund)`` zurueck, oder ``(None, None)``, wenn nichts aufzuheben
    war. Ohne ``user_id`` wird nichts geschrieben: ein Eintrag ohne Besitzer waere in
    keinem Vault sichtbar und wuerde die Mandantentrennung unterlaufen.
    """
    reason = detect(text)
    if not reason or not user_id:
        return None, None

    from app.core.knowledge_write import write_entry

    entry, _created = await write_entry(
        db,
        user_id=user_id,
        title=build_title(text, reason),
        content=build_content(text, reason, source),
        tags=[CAPTURE_TAG, UNREAD_TAG],
        author=author,
    )
    logger.info("[Capture] %s aus %s als Eintrag %s abgelegt", reason, source, entry.id)
    return entry, reason
