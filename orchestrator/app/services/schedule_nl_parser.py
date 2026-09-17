"""Natural-language -> cron_expression translation for schedules (issue #196).

The scheduling tools (`create_schedule` et al.) already accept a raw
cron_expression — what was missing is turning a phrase like "jeden Montag um
9 Uhr" into one. Two tiers:

1. A deterministic parser for the common phrasings (daily/weekly-on-weekday/
   weekdays-only/hourly/monthly-on-day, DE+EN). Fast, no external dependency —
   most real requests ("melde dich jeden Montag ...") fall here.
2. An LLM fallback for anything else, reusing the same one-off-call pattern as
   ``memory_compressor._llm_summarize`` (many deployments have no
   ``anthropic_api_key`` configured — see self_test_service's own "No
   Anthropic API key configured" case — so this tier is best-effort, not a
   hard dependency).

Raises ``ValueError`` when neither tier can make sense of the text, so the
caller (the API layer) can surface a 422 telling the user to fall back to a
manual cron expression instead of silently creating a wrong schedule.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_WOCHENTAGE = {
    "sonntag": 0, "sunday": 0,
    "montag": 1, "monday": 1,
    "dienstag": 2, "tuesday": 2,
    "mittwoch": 3, "wednesday": 3,
    "donnerstag": 4, "thursday": 4,
    "freitag": 5, "friday": 5,
    "samstag": 6, "saturday": 6,
}

_WOCHENTAG_NAMEN = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"]

# Vier Schreibweisen fuer eine Uhrzeit, in eigenen benannten Gruppen statt
# geteilter Nummerierung -- Alternation mit gemeinsamer Gruppenzaehlung
# verschiebt sonst je nach Treffer, welche Gruppe die Stunde ist.
_ZEIT_RE = re.compile(
    r"""
    \bum\s+(?P<h1>\d{1,2})(?::(?P<m1>\d{2}))?\b
    |\b(?P<h2>\d{1,2}):(?P<m2>\d{2})\b
    |\b(?P<h3>\d{1,2})\s*uhr\b
    |\b(?P<h4>\d{1,2})\s*(?P<ampm>am|pm)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_MONATSTAG_RE = re.compile(
    r"\b(?:am|on the)\s+(\d{1,2})\.?(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)

_STANDARD_STUNDE = 9  # Ohne explizite Uhrzeit: ein "Check-in" um 9 ist ein vernuenftiger Default.


def _extract_zeit(text: str) -> tuple[int, int] | None:
    match = _ZEIT_RE.search(text)
    if not match:
        return None
    if match.group("h1"):
        stunde, minute = int(match.group("h1")), int(match.group("m1") or 0)
    elif match.group("h2"):
        stunde, minute = int(match.group("h2")), int(match.group("m2"))
    elif match.group("h3"):
        stunde, minute = int(match.group("h3")), 0
    else:
        stunde, minute = int(match.group("h4")), 0
        if match.group("ampm").lower() == "pm" and stunde < 12:
            stunde += 12
        elif match.group("ampm").lower() == "am" and stunde == 12:
            stunde = 0
    if 0 <= stunde <= 23 and 0 <= minute <= 59:
        return stunde, minute
    return None


def _extract_wochentag(text: str) -> int | None:
    for name, num in _WOCHENTAGE.items():
        if re.search(rf"\b{name}\b", text):
            return num
    return None


def parse_deterministic(text: str) -> str | None:
    """Return a 5-field cron_expression for a common phrasing, or None."""
    low = text.lower()
    zeit = _extract_zeit(low)
    stunde, minute = zeit if zeit else (_STANDARD_STUNDE, 0)

    wochentag = _extract_wochentag(low)
    if wochentag is not None:
        return f"{minute} {stunde} * * {wochentag}"

    if re.search(r"\b(werktags|wochentags|weekday|monday to friday|mon-fri)\b", low):
        return f"{minute} {stunde} * * 1-5"

    if re.search(r"\b(stündlich|stuendlich|jede stunde|every hour|hourly)\b", low):
        return "0 * * * *"

    if re.search(r"\b(monatlich|jeden monat|monthly)\b", low):
        monatstag = _MONATSTAG_RE.search(low)
        tag = int(monatstag.group(1)) if monatstag else 1
        if 1 <= tag <= 28:
            return f"{minute} {stunde} {tag} * *"

    if re.search(r"\b(täglich|taeglich|jeden tag|every day|daily)\b", low):
        return f"{minute} {stunde} * * *"

    return None


def _erklaerung(cron: str) -> str:
    """Human-readable back-translation for the preview UI."""
    teile = cron.split()
    if len(teile) != 5:
        return cron
    minute, stunde, tag, _monat, wochentag = teile
    uhrzeit = f"{int(stunde):02d}:{int(minute):02d}" if stunde.isdigit() and minute.isdigit() else None
    if uhrzeit and wochentag.isdigit():
        return f"Jeden {_WOCHENTAG_NAMEN[int(wochentag) % 7]} um {uhrzeit} Uhr"
    if uhrzeit and wochentag == "1-5":
        return f"Werktags (Mo-Fr) um {uhrzeit} Uhr"
    if uhrzeit and tag.isdigit() and tag != "0":
        return f"Am {int(tag)}. jeden Monats um {uhrzeit} Uhr"
    if stunde == "*" and minute == "0" and tag == "*" and wochentag == "*":
        return "Jede volle Stunde"
    if uhrzeit and tag == "*" and wochentag == "*":
        return f"Täglich um {uhrzeit} Uhr"
    return cron


_LLM_SYSTEM_PROMPT = (
    "Du uebersetzt eine deutsche oder englische Zeitangabe fuer einen wiederkehrenden "
    "Termin in einen 5-Feld-Cron-Ausdruck (Minute Stunde Tag Monat Wochentag; Wochentag "
    "0=Sonntag..6=Samstag). Antworte NUR mit dem Cron-Ausdruck, ohne Erklaerung und ohne "
    "Anfuehrungszeichen. Wenn der Text keine sinnvolle Zeitangabe enthaelt, antworte exakt "
    "mit UNBEKANNT."
)


async def _llm_parse(text: str) -> str | None:
    try:
        from app.config import settings
        anthropic_key = getattr(settings, "anthropic_api_key", None) or ""
    except Exception:
        anthropic_key = ""
    if not anthropic_key:
        return None
    try:
        import httpx
    except ImportError:
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 60,
                    "system": _LLM_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": text}],
                },
            )
            if resp.status_code != 200:
                logger.warning("[ScheduleNLParser] Haiku returned %s", resp.status_code)
                return None
            data = resp.json()
            roh = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    roh = block.get("text", "").strip()
                    break
    except Exception as e:
        logger.warning("[ScheduleNLParser] LLM-Aufruf fehlgeschlagen: %s", e)
        return None

    if not roh or roh.upper() == "UNBEKANNT":
        return None
    from croniter import croniter
    if not croniter.is_valid(roh):
        logger.warning("[ScheduleNLParser] LLM lieferte ungueltigen Cron-Ausdruck: %r", roh)
        return None
    return roh


async def parse_natural_language_schedule(text: str) -> dict:
    """Translate free text into ``{"cron_expression", "explanation", "source"}``.

    Raises ``ValueError`` when the text can't be resolved by either tier —
    callers should surface this rather than guessing, so a misread phrase
    never silently creates a schedule at the wrong time.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Leerer Text.")

    cron = parse_deterministic(text)
    if cron:
        return {"cron_expression": cron, "explanation": _erklaerung(cron), "source": "regel"}

    llm_cron = await _llm_parse(text)
    if llm_cron:
        return {"cron_expression": llm_cron, "explanation": _erklaerung(llm_cron), "source": "llm"}

    raise ValueError(
        "Konnte daraus keinen Zeitplan ableiten. Bitte direkt als Cron-Ausdruck eingeben "
        "(z. B. '0 9 * * 1' fuer jeden Montag 9 Uhr)."
    )
