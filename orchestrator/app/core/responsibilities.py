"""Verantwortungsbereiche — was ein Agent DAUERHAFT besitzt.

Ein Bereich ist kein Todo: er kehrt wieder und wird nie „fertig". Der proaktive Lauf
leitet daraus die konkreten Aufgaben des Tages ab (PROACTIVE_PROMPT STEP 1) — das ist der
Unterschied zwischen einem Agenten, der auf Arbeit wartet, und einem, der seinen Job kennt.

Liegt in ``core``, nicht in der API-Schicht: die Darstellung im Prompt
(``scheduler_service._responsibilities_note``) und die Pruefung beim Speichern muessen
sich auf DIESELBE Definition stuetzen, und beide sollen ohne die Docker-Abhaengigkeit der
API importierbar bleiben.
"""

from fastapi import HTTPException


# A responsibility is a STANDING duty, not a task: it recurs and is never "done".
RESPONSIBILITY_RHYTHMS = ("daily", "weekly", "monthly", "continuous")
RESPONSIBILITY_PRIORITIES = ("high", "normal", "low")
MAX_RESPONSIBILITIES = 20  # a person with 30 duties has none — and the prompt stays readable


def validated_responsibilities(items: list[dict] | None) -> list[dict]:
    """Validate + normalize the agent's areas of responsibility.

    Rejects malformed entries with 422 instead of storing something the proactive run
    would silently skip — a duty that quietly does nothing is worse than no duty.
    """
    if not items:
        return []
    if len(items) > MAX_RESPONSIBILITIES:
        raise HTTPException(
            status_code=422,
            detail=f"Höchstens {MAX_RESPONSIBILITIES} Verantwortungsbereiche pro Agent",
        )
    out: list[dict] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422, detail="Jeder Verantwortungsbereich muss ein Objekt sein")
        title = str(raw.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="Verantwortungsbereich braucht einen Titel")
        if len(title) > 120:
            raise HTTPException(status_code=422, detail="Titel darf höchstens 120 Zeichen haben")
        key = title.casefold()
        if key in seen:
            raise HTTPException(status_code=422, detail=f"Doppelter Verantwortungsbereich: {title}")
        seen.add(key)
        rhythm = str(raw.get("rhythm") or "daily").strip().lower()
        if rhythm not in RESPONSIBILITY_RHYTHMS:
            raise HTTPException(
                status_code=422,
                detail=f"Unbekannter Takt '{rhythm}' — erlaubt: {', '.join(RESPONSIBILITY_RHYTHMS)}",
            )
        priority = str(raw.get("priority") or "normal").strip().lower()
        if priority not in RESPONSIBILITY_PRIORITIES:
            raise HTTPException(
                status_code=422,
                detail=f"Unbekannte Priorität '{priority}' — erlaubt: {', '.join(RESPONSIBILITY_PRIORITIES)}",
            )
        out.append({
            "title": title,
            "rhythm": rhythm,
            "priority": priority,
            "notes": str(raw.get("notes") or "").strip()[:500],
        })
    return out


_RHYTHM_LABELS = {
    "daily": "täglich",
    "weekly": "wöchentlich",
    "monthly": "monatlich",
    "continuous": "laufend",
}
_PRIORITY_LABELS = {"high": "hoch", "normal": "normal", "low": "niedrig"}


def responsibilities_note(proactive_config: dict) -> str:
    """Render the agent's standing areas of responsibility as a prompt block, or "".

    These are duties, not tasks: they recur and are never "done". The proactive run
    turns them into concrete todos for the day (STEP 1) — that is the difference
    between an agent that waits for work and one that knows what its job is.
    """
    duties = (proactive_config or {}).get("responsibilities") or []
    if not duties:
        return ""
    lines = [
        "## Deine Verantwortungsbereiche",
        "Dafür bist DU dauerhaft zuständig — unabhängig davon, ob jemand ein Todo angelegt hat.",
        "Leite in STEP 1 daraus die konkreten Aufgaben für heute ab (Takt beachten: was täglich",
        "dran ist, gehört in JEDEN Tagesplan; wöchentliches/monatliches nur, wenn es fällig ist —",
        "im Zweifel prüfe in `.agent_state.md` oder im Gedächtnis, wann du es zuletzt gemacht hast).",
        "Ein Bereich wird nie fertig: abhaken heißt, den heutigen Durchgang erledigt zu haben.",
        "",
    ]
    for d in duties:
        rhythm = _RHYTHM_LABELS.get(str(d.get("rhythm", "")), str(d.get("rhythm", "")))
        priority = _PRIORITY_LABELS.get(str(d.get("priority", "")), str(d.get("priority", "")))
        line = f"- **{d.get('title', '')}** ({rhythm}, Priorität {priority})"
        notes = str(d.get("notes") or "").strip()
        if notes:
            line += f": {notes}"
        lines.append(line)
    return "\n".join(lines)
