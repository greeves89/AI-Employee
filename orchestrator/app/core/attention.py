"""Was gerade eine Entscheidung oder einen Handgriff braucht (#11).

Der Concierge war eine Kennzahlen-Kachel mit Ampel: vier Zahlen groß, die Alarmliste
klein. Das ist der falsche Zuschnitt. „Aufgaben 24 h: 33" und „Kosten 24 h: 57 $"
verlangen **keine Handlung** — das ist Auswertung, und dafür gibt es das Dashboard.
Sobald Zahlen den Platz füllen, muss die Ampel den Alarm tragen, und dann wird sie
großzügig ausgelöst: so landete „angehalten" in derselben Liste wie „abgestürzt".

**Eine Regel:** hier steht nur, was eine Entscheidung oder einen Handgriff braucht.
Jeder Punkt trägt genau eine Sache, die man dagegen tun kann. Ist nichts da, steht
da „alles ruhig" — und sonst nichts.

Der wertvollste Punkt ist dabei nicht der lauteste: ein **abgelaufener Zugang**
scheitert still. Die Claude-Agenten hatten das, und das Dazwischenreden im laufenden
Turn war wochenlang tot, ohne dass irgendwo etwas rot war.

Rein rechnend, ohne Datenbank — damit jede Regel für sich prüfbar ist. Die Abfragen
stehen in ``api/concierge.py``.
"""

from datetime import datetime, timedelta, timezone

# Zwei Dringlichkeiten, mehr braucht es nicht. Wer drei Stufen anbietet, bekommt
# eine Ampel, bei der niemand mehr weiss, was Gelb bedeutet.
BROKEN = "broken"   # kaputt oder scheitert still → rot
WAITING = "waiting"  # wartet auf eine Entscheidung → gelb

# Ab wann ein ablaufender Zugang genannt wird. Drei Tage sind lang genug, um es in
# Ruhe zu erledigen, und kurz genug, dass es nicht dauerhaft dasteht.
TOKEN_WARN_AHEAD = timedelta(days=3)


def item(
    kind: str,
    severity: str,
    title: str,
    detail: str,
    *,
    agent_id: str | None = None,
    action: str | None = None,
    action_label: str | None = None,
    link: str | None = None,
    count: int = 1,
) -> dict:
    """Ein Punkt der Liste.

    ``action`` ist eine der wenigen sicheren Aktionen (direkt ausführbar),
    ``link`` führt auf die Seite, wo es entschieden wird. Beides gleichzeitig ist
    zulässig — manches lässt sich hier erledigen UND anderswo genauer ansehen.
    """
    return {
        "kind": kind,
        "severity": severity,
        "title": title,
        "detail": detail,
        "agent_id": agent_id,
        "action": action,
        "action_label": action_label,
        "link": link,
        "count": count,
    }


def verdict_for(items: list[dict]) -> str:
    """Die Ampel aus den Punkten ableiten, nicht umgekehrt.

    Vorher wurde die Ampel aus einer eigenen Bedingung gebildet und die Liste
    daneben — dann können beide auseinanderlaufen, und genau das ist passiert.
    """
    if any(i["severity"] == BROKEN for i in items):
        return "handlungsbedarf"
    if items:
        return "wartet auf dich"
    return "alles ruhig"


def token_state(expires_at: datetime | None, now: datetime | None = None) -> str | None:
    """``BROKEN`` wenn abgelaufen, ``WAITING`` wenn bald, sonst ``None``.

    Ein Zugang ohne Ablaufdatum gilt als in Ordnung — nicht jeder hat eins, und
    „unbekannt" als Alarm zu werten hiesse, dauerhaft rot zu sein.
    """
    if expires_at is None:
        return None
    now = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        return BROKEN
    if expires_at <= now + TOKEN_WARN_AHEAD:
        return WAITING
    return None


def budget_state(spent: float | None, budget: float | None) -> str | None:
    """``BROKEN`` wenn aufgebraucht, ``WAITING`` ab 90 %, sonst ``None``.

    Ohne Deckel gibt es nichts zu melden — „unbegrenzt" ist eine Entscheidung, kein
    Versäumnis.
    """
    if not budget or budget <= 0:
        return None
    used = float(spent or 0)
    if used >= budget:
        return BROKEN
    if used >= budget * 0.9:
        return WAITING
    return None


def skips_proactive(config: dict | None) -> bool:
    """Läuft dieser angehaltene Agent seinem Auftrag hinterher?

    Seit v1.154.1 werden gestoppte Agenten nicht mehr proaktiv angesteuert. Ein
    angehaltener Agent MIT Verantwortungsbereichen tut damit still nichts — und
    genau so sammelten beim Kunden zwei Agenten vier Wochen lang fehlgeschlagene
    Läufe, ohne dass es auffiel.
    """
    proactive = (config or {}).get("proactive") or {}
    return bool(proactive.get("enabled") and proactive.get("responsibilities"))
