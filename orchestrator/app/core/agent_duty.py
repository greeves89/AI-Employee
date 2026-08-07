"""Dienstzustand eines Agenten und der EINE Eskalationspfad.

Fuenf Luecken hingen an demselben fehlenden Begriff: Vertretung bei Ausfall, Eskalation
bei Schweigen, eigene Arbeitszeit, Ueberlast und die Abwesenheit des Menschen. Getrennt
gebaut waeren das fuenf Pflaster — hier ist es ein Zustand und eine Kette, die alle fuenf
benutzen.

**Dienstzustand** (aus vorhandenen Signalen abgeleitet, nichts doppelt erhoben):
  ``ok`` · ``overloaded`` (Warteschlange laeuft voll) · ``blocked`` (Arbeit haengt seit
  Stunden) · ``down`` (Container weg oder gestoppt, obwohl Zeitplan aktiv) · ``off_duty``
  (ausserhalb SEINER Dienstzeit).

**Eskalationskette:** Ansprechpartner → Vertreter-Agent → Team-Lead → Admin. Zwei
Ausloeser, ein Pfad: der Agent faellt aus (dann uebernimmt der Vertreter) oder der Mensch
antwortet nicht (dann geht es eine Stufe hoeher).
"""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

# Zustaende
OK = "ok"
OVERLOADED = "overloaded"
BLOCKED = "blocked"
DOWN = "down"
OFF_DUTY = "off_duty"

# Ab wann die Warteschlange als Ueberlast gilt. Drei wartende Aufgaben sind kein Stau,
# zehn sind einer — dazwischen faengt es an, den Tagesplan wertlos zu machen.
OVERLOAD_QUEUE_DEPTH = 5
# Wie viele haengende Aufgaben aus 'blockiert' einen echten Ausfall machen.
BLOCKED_STALE_TASKS = 1
# Nach so vielen unbeantworteten Rueckfragen geht es eine Stufe hoeher.
ESCALATE_AFTER_UNANSWERED = 2

_LIVE_STATES = ("running", "idle", "working")


def _cfg(agent) -> dict:
    return ((agent.config or {}) if agent else {}) or {}


def _state_str(agent) -> str:
    raw = getattr(agent, "state", "") if agent else ""
    return str(getattr(raw, "value", raw)).lower()


# ---------------------------------------------------------------------------
# Dienstzeit DES AGENTEN (nicht die Erreichbarkeit des Menschen)
# ---------------------------------------------------------------------------

def working_hours(agent) -> dict:
    """``{start, end, timezone, weekdays_only}`` oder {} — die eigene Dienstzeit.

    Bisher gab es nur die Erreichbarkeit des Ansprechpartners. Ein Mitarbeiter hat aber
    auch selbst Feierabend: ohne das laeuft ein Agent 24/7 und niemand kann sagen, ob er
    ueberlastet ist oder einfach nur die halbe Nacht arbeitet.
    """
    return dict(_cfg(agent).get("working_hours") or {})


def _within(now: datetime, start: str, end: str, tz_name: str) -> bool:
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:
        tz = timezone.utc
    local = now.astimezone(tz)
    try:
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
    except (ValueError, AttributeError):
        return True
    s, e, cur = time(sh, sm), time(eh, em), local.time()
    if s <= e:
        return s <= cur <= e
    return cur >= s or cur <= e          # Fenster ueber Mitternacht


def is_on_duty(agent, now: datetime | None = None) -> bool:
    """Ist der Agent gerade im Dienst? Ohne Angabe: immer (bisheriges Verhalten)."""
    hours = working_hours(agent)
    start, end = (hours.get("start") or "").strip(), (hours.get("end") or "").strip()
    if not start or not end:
        return True
    now = now or datetime.now(timezone.utc)
    if hours.get("weekdays_only"):
        try:
            tz = ZoneInfo(hours.get("timezone") or "UTC")
        except Exception:
            tz = timezone.utc
        if now.astimezone(tz).weekday() >= 5:
            return False
    return _within(now, start, end, hours.get("timezone") or "UTC")


# ---------------------------------------------------------------------------
# Abwesenheit des Menschen
# ---------------------------------------------------------------------------

def contact_absence(agent) -> dict:
    """``{from, to}`` (ISO-Daten) oder {} — Urlaub/Abwesenheit des Ansprechpartners."""
    return dict((_cfg(agent).get("proactive") or {}).get("contact_absence") or {})


def is_contact_absent(agent, now: datetime | None = None) -> bool:
    """Ist der Ansprechpartner gerade weg? Dann sammelt der Agent Rueckfragen, statt
    sie ins Leere zu schicken."""
    window = contact_absence(agent)
    start, end = (window.get("from") or "").strip(), (window.get("to") or "").strip()
    if not start or not end:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        from datetime import date as _date
        return _date.fromisoformat(start) <= now.date() <= _date.fromisoformat(end)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Dienstzustand
# ---------------------------------------------------------------------------

def assess(agent, *, queue_depth: int = 0, stale_tasks: int = 0,
           now: datetime | None = None, schedule_active: bool = False) -> dict:
    """Dienstzustand aus vorhandenen Signalen ableiten.

    ``queue_depth`` kommt aus Redis, ``stale_tasks`` aus dem Watchdog — beides wird
    ohnehin schon erhoben. Hier entsteht nur die gemeinsame Lesart.
    """
    now = now or datetime.now(timezone.utc)
    state = _state_str(agent)

    if state not in _LIVE_STATES:
        # Gestoppt ist nur dann ein Ausfall, wenn eigentlich Arbeit anstuende.
        return {"state": DOWN if schedule_active else OFF_DUTY,
                "reason": f"Agent ist {state or 'unbekannt'}"}
    if stale_tasks >= BLOCKED_STALE_TASKS:
        return {"state": BLOCKED,
                "reason": f"{stale_tasks} Aufgabe(n) haengen seit Stunden ohne Lebenszeichen"}
    if queue_depth >= OVERLOAD_QUEUE_DEPTH:
        return {"state": OVERLOADED,
                "reason": f"{queue_depth} Aufgaben warten in der Schlange"}
    if not is_on_duty(agent, now):
        return {"state": OFF_DUTY, "reason": "ausserhalb seiner Dienstzeit"}
    return {"state": OK, "reason": ""}


def needs_handover(duty: dict) -> bool:
    """Zustaende, bei denen die Arbeit jemand anders uebernehmen muss."""
    return duty.get("state") in (DOWN, BLOCKED)


# ---------------------------------------------------------------------------
# Eskalationskette
# ---------------------------------------------------------------------------

def deputy_agent_id(agent) -> str:
    """Wer uebernimmt, wenn dieser Agent ausfaellt? Leer = Team-Lead."""
    return str(_cfg(agent).get("deputy_agent_id") or "").strip()


def escalation_chain(agent, *, team_lead_id: str = "") -> list[dict]:
    """Die Kette in Reihenfolge — jede Stufe mit Art und Ziel.

    Eine Definition fuer beide Ausloeser: faellt der Agent aus, greift ``agent``
    (Vertreter/Lead); schweigt der Mensch, greift ``human`` (Ansprechpartner, dann Lead,
    dann Admin).
    """
    chain: list[dict] = [{"kind": "human", "target": getattr(agent, "user_id", None) or "",
                          "label": "Ansprechpartner"}]
    deputy = deputy_agent_id(agent)
    if deputy:
        chain.append({"kind": "agent", "target": deputy, "label": "Vertreter"})
    if team_lead_id and team_lead_id != getattr(agent, "id", None):
        chain.append({"kind": "agent", "target": team_lead_id, "label": "Team-Lead"})
    chain.append({"kind": "admin", "target": "", "label": "Administration"})
    return chain


def duty_note(agent, duty: dict, *, now: datetime | None = None) -> str:
    """Prompt-Block: was der Agent ueber seinen eigenen Zustand wissen muss.

    Geht an alle Laufzeiten — er soll seine Lage selbst kennen, statt dass nur der
    Orchestrator sie sieht.
    """
    lines: list[str] = []
    hours = working_hours(agent)
    if hours.get("start") and hours.get("end"):
        tz = hours.get("timezone") or "UTC"
        tail = ", nur werktags" if hours.get("weekdays_only") else ""
        lines.append(
            f"Deine Dienstzeit ist {hours['start']}–{hours['end']} ({tz}{tail}). Ausserhalb "
            "erledigst du nur, was keine Abstimmung braucht, und legst den Rest auf morgen."
        )
    if duty.get("state") == OVERLOADED:
        lines.append(
            f"ACHTUNG, DU BIST UEBERLASTET: {duty.get('reason')}. Nimm nichts Neues an, "
            "arbeite die Schlange nach Prioritaet ab und sag deinem Ansprechpartner "
            "EINMAL Bescheid, dass es zu viel ist — mit dem Vorschlag, was warten kann."
        )
    if is_contact_absent(agent, now):
        window = contact_absence(agent)
        lines.append(
            f"Dein Ansprechpartner ist bis {window.get('to')} nicht da. Stell KEINE "
            "Rueckfragen an ihn — sammle sie in `.agent_state.md` unter 'Offene Fragen' und "
            "leg sie ihm gebuendelt vor, wenn er zurueck ist. Arbeite solange alles ab, was "
            "ohne seine Entscheidung geht."
        )
    if not lines:
        return ""
    return "\n=== DEIN DIENST ===\n" + "\n".join(f"- {line}" for line in lines) + "\n=== ENDE ===\n"
