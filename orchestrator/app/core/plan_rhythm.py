"""Abends planen, morgens nachschaerfen — der Arbeitsrhythmus JEDES Agenten.

Ein Agent, der seinen Tag erst mittendrin plant, plant den halben Tag im Nachhinein.
Ein echter Mitarbeiter macht das anders: am Abend legt er fest, was morgen dran ist,
und am naechsten Morgen sieht er nochmal drueber — mit dem, was ueber Nacht gelaufen
ist, und mit dem, was der Nutzer inzwischen im Kalender verschoben hat.

Bisher hatte genau EIN Agent das, weil er es sich im Chat selbst eingerichtet hatte.
Hier steht es fuer alle, und zwar an zwei Stellen verzahnt:

* **Zeitpunkt** — ``ensure``-Zeitplaene ``[Rhythmus] Abendplanung`` / ``[Rhythmus]
  Morgencheck`` laufen ueber dieselbe Zeitplan-Maschinerie wie alles andere. Kein
  zweiter Ausloeser, kein Sonderweg.
* **Inhalt** — ``rhythm_note`` haengt an JEDEM proaktiven Lauf und sagt ihm, in welcher
  Phase er gerade steckt. Faellt ein Rhythmus-Lauf aus, holt der naechste proaktive Lauf
  im Abendfenster die Planung trotzdem nach.

Die Zeiten leitet der Agent aus seiner eigenen Dienstzeit ab (``core.agent_duty``):
Abendplanung eine halbe Stunde vor Dienstschluss, Morgencheck zum Dienstbeginn. Ohne
konfigurierte Dienstzeit gelten 21:30 und 07:00 Ortszeit.

**Wochenende:** der Rhythmus laeuft an sieben Tagen. Nur wer ``weekdays_only`` in seiner
Dienstzeit stehen hat, macht Samstag und Sonntag frei — das ist eine bewusste Einstellung
des Nutzers, keine Voreinstellung. (Ein Agent hatte Montag leer, weil niemand am Sonntag
plante.)
"""

from datetime import date as date_cls, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core import agent_duty

EVENING = "evening"
MORNING = "morning"
DAY = "day"

# Ohne eigene Dienstzeit: Feierabendplanung halb zehn, Morgencheck sieben.
DEFAULT_EVENING = "21:30"
DEFAULT_MORNING = "07:00"
# So lange vor Dienstschluss wird geplant — danach ist er weg und plant nichts mehr.
EVENING_LEAD_MINUTES = 30
# Wie lange nach dem Morgencheck ein Lauf noch als „Morgen" zaehlt.
MORNING_WINDOW_MINUTES = 150
# Und wie lange davor (wer frueher dran ist, macht den Check trotzdem).
MORNING_LEAD_MINUTES = 45

SCHEDULE_PREFIX = "[Rhythmus] "
EVENING_SCHEDULE_NAME = f"{SCHEDULE_PREFIX}Abendplanung"
MORNING_SCHEDULE_NAME = f"{SCHEDULE_PREFIX}Morgencheck"


# ---------------------------------------------------------------------------
# Zeitzone und Zeiten
# ---------------------------------------------------------------------------

def timezone_name(config: dict | None) -> str:
    """In welcher Zeitzone denkt dieser Agent?

    Erreichbarkeit des Ansprechpartners zuerst — nach dessen Uhr richtet sich der Tag —,
    sonst die eigene Dienstzeit, sonst UTC. Dieselbe Reihenfolge nutzt die Sprachfront,
    damit gesprochene und angezeigte Uhrzeit nie auseinanderlaufen.
    """
    cfg = config or {}
    return str(
        ((cfg.get("proactive") or {}).get("contact_hours") or {}).get("timezone")
        or (cfg.get("working_hours") or {}).get("timezone")
        or "UTC"
    ).strip() or "UTC"


def tzinfo(config: dict | None):
    try:
        return ZoneInfo(timezone_name(config))
    except Exception:  # noqa: BLE001 — eine kaputte Zeitzone darf keinen Lauf kosten
        return timezone.utc


def _minutes(hhmm: str, fallback: int) -> int:
    try:
        h, m = (int(x) for x in str(hhmm).split(":"))
        return h * 60 + m
    except (ValueError, AttributeError):
        return fallback


def _hhmm(minutes: int) -> str:
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def manual_morning(agent) -> dict:
    """``{time, weekdays_only}`` aus „Tagesplanung am Morgen" — oder {}.

    Diese Einstellung gab es vor dem Rhythmus und legte einen EIGENEN Zeitplan an.
    Sie bleibt bestehen, steuert jetzt aber den Morgencheck: eine Uhrzeit, ein Lauf.
    """
    return dict(((getattr(agent, "config", None) or {}).get("proactive") or {})
                .get("morning_planning") or {})


def rhythm_times(agent) -> dict:
    """``{evening, morning, timezone, weekdays_only}`` — wann dieser Agent plant.

    Abgeleitet aus SEINER Dienstzeit, nicht global gesetzt: wer bis 17:00 Dienst hat,
    plant um 16:30 und nicht um halb zehn abends, wenn er laengst aus ist.
    """
    hours = agent_duty.working_hours(agent)
    start, end = (hours.get("start") or "").strip(), (hours.get("end") or "").strip()
    # Die von Hand gesetzte „Tagesplanung am Morgen" ist die ausdrueckliche Ansage des
    # Nutzers — sie STEUERT den Morgencheck, statt daneben einen zweiten Planungslauf
    # anzulegen. Frueher war das ein eigener Zeitplan; zwei Planungslaeufe am selben
    # Morgen sind einer zu viel.
    manual = manual_morning(agent)
    manual_time = (manual.get("time") or "").strip()

    evening = (
        _minutes(end, _minutes(DEFAULT_EVENING, 1290)) - EVENING_LEAD_MINUTES
        if end else _minutes(DEFAULT_EVENING, 1290)
    )
    if manual_time:
        morning = _minutes(manual_time, _minutes(DEFAULT_MORNING, 420))
    elif start:
        morning = _minutes(start, _minutes(DEFAULT_MORNING, 420))
    else:
        morning = _minutes(DEFAULT_MORNING, 420)
    if evening <= morning:
        # Dienst ueber Mitternacht oder absurd kurz: dann lieber die Standardzeiten,
        # sonst plant er den Tag, den er gerade erst begonnen hat.
        evening, morning = _minutes(DEFAULT_EVENING, 1290), _minutes(DEFAULT_MORNING, 420)
    return {
        "evening": _hhmm(evening),
        "morning": _hhmm(morning),
        "timezone": timezone_name(getattr(agent, "config", None)),
        "weekdays_only": bool(hours.get("weekdays_only") or manual.get("weekdays_only")),
    }


def cron_expressions(agent) -> dict:
    """Die beiden Cron-Ausdruecke — sieben Tage, ausser der Nutzer hat Werktage gesetzt."""
    times = rhythm_times(agent)
    days = "1-5" if times["weekdays_only"] else "*"
    ev_h, ev_m = times["evening"].split(":")
    mo_h, mo_m = times["morning"].split(":")
    return {
        "evening": f"{int(ev_m)} {int(ev_h)} * * {days}",
        "morning": f"{int(mo_m)} {int(mo_h)} * * {days}",
        "timezone": times["timezone"],
    }


def phase(agent, now: datetime | None = None) -> str:
    """In welcher Phase steckt dieser Lauf: ``evening``, ``morning`` oder ``day``?

    Damit weiss auch ein ganz normaler proaktiver Lauf, dass er um 22 Uhr den naechsten
    Tag zu planen hat — die Planung haengt nicht daran, dass der Rhythmus-Zeitplan
    ueberhaupt gefeuert hat.
    """
    now = now or datetime.now(timezone.utc)
    times = rhythm_times(agent)
    local = now.astimezone(tzinfo(getattr(agent, "config", None)))
    cur = local.hour * 60 + local.minute
    evening = _minutes(times["evening"], 1290)
    morning = _minutes(times["morning"], 420)
    if cur >= evening:
        return EVENING
    if morning - MORNING_LEAD_MINUTES <= cur <= morning + MORNING_WINDOW_MINUTES:
        return MORNING
    return DAY


def target_date(agent, now: datetime | None = None) -> date_cls:
    """Welchen Tag betrifft die Planung dieses Laufs — heute oder morgen?"""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(tzinfo(getattr(agent, "config", None)))
    return (local + timedelta(days=1)).date() if phase(agent, now) == EVENING else local.date()


# ---------------------------------------------------------------------------
# Prompt-Bausteine
# ---------------------------------------------------------------------------

def _night_block(night: list[dict] | None) -> str:
    """Was seit dem letzten Planungslauf gelaufen ist — kompakt, mit Ausgang.

    Der Agent koennte sich das selbst zusammensuchen; er tut es aber nicht zuverlaessig,
    und genau daran haengt der Morgencheck. Also liegt es ihm vor.
    """
    if not night:
        return (
            "Seit deiner letzten Planung ist nichts gelaufen. Hattest du etwas eingeplant, "
            "sieh nach, warum es nicht lief."
        )
    lines = []
    for run in night[:12]:
        state = {"completed": "fertig", "failed": "GESCHEITERT",
                 "cancelled": "abgebrochen"}.get(run.get("status"), run.get("status") or "?")
        lines.append(f"- {run.get('title', '?')[:90]} — {state}")
    return "Das ist seit deiner letzten Planung gelaufen:\n" + "\n".join(lines)


def planning_instruction(plan_date: date_cls, *, focus: str = "") -> str:
    """Die Schritte, wie ein Tag geplant wird — EINE Fassung fuer alle Wege.

    Der Rhythmus-Lauf plant so, und die Sprachfront gibt auf „plan mir den Tag" genau
    dieselbe Anweisung weiter. Vorher hatte die Stimme eine eigene, kuerzere Fassung
    ohne Uhrzeit-Pflicht — die daraus entstandenen Bloecke standen im Kalender und
    liefen nie, weil ohne `planned_start` kein Ausloeser entsteht.
    """
    iso = plan_date.isoformat()
    schwerpunkt = f"\nSchwerpunkt laut Nutzer: {focus.strip()}\n" if focus.strip() else ""
    return (
        "1. Lies `/workspace/.agent_state.md` und `list_todos` — was ist liegen "
        "geblieben, was ist angefangen, was ist neu dazugekommen?\n"
        "2. Sieh dir deine Verantwortungsbereiche an: welche sind "
        f"am {iso} faellig? Beachte den Rhythmus jedes Bereichs und wann du ihn "
        "zuletzt gemacht hast.\n"
        f"3. Rufe `get_day_plan` mit `plan_date: \"{iso}\"` auf — steht dort schon "
        "etwas (vom Nutzer eingetragen oder von dir), baust du darauf auf, statt es zu "
        "ueberschreiben. Was der Nutzer gestrichen hat, bleibt gestrichen.\n"
        f"4. Schreibe den Plan mit `plan_day` und `plan_date: \"{iso}\"`. JEDER Block "
        "braucht `planned_start` — ohne Uhrzeit entsteht kein Ausloeser und der Block "
        "laeuft nie von allein. Dazu `estimated_minutes`: mindestens 15, lieber ein "
        "ehrlicher 45-Minuten-Block als drei optimistische Zehn-Minuten-Schnipsel. "
        "Setze `priority` bewusst.\n"
        "5. Halte in `.agent_state.md` unter „Next Steps\" fest, worauf es ankommt.\n"
        + schwerpunkt
    )


def evening_prompt(agent, plan_date: date_cls, night: list[dict] | None = None) -> str:
    """Der Abendlauf: den NAECHSTEN Tag planen. Ein Lauf, ein Ergebnis."""
    return (
        "FEIERABENDPLANUNG. Das ist dein letzter Lauf heute, und er hat genau eine "
        f"Aufgabe: den Tagesplan fuer **{plan_date.isoformat()}** zu schreiben.\n\n"
        "So gehst du vor:\n"
        + planning_instruction(plan_date)
        + (f"\n{_night_block(night)}\n" if night else "")
        + "\nArbeite jetzt KEINE Aufgaben ab — das ist die Planung. Melde am Ende in zwei "
        "Saetzen, was morgen dran ist."
    )


def morning_prompt(agent, plan_date: date_cls, night: list[dict] | None = None) -> str:
    """Der Morgenlauf: den Plan von gestern Abend gegen die Nacht und die Realitaet halten."""
    return (
        "MORGENCHECK. Bevor du loslegst, bringst du den Plan fuer heute "
        f"(**{plan_date.isoformat()}**) auf Stand.\n\n"
        f"{_night_block(night)}\n\n"
        "So gehst du vor:\n"
        f"1. `get_day_plan` fuer {plan_date.isoformat()} — was hast du gestern Abend "
        "geplant, und was hat der Nutzer daran geaendert? Ein Block, den er gestrichen "
        "hat, ist vom Tisch: nicht arbeiten, nicht wieder eintragen.\n"
        "2. Gleiche mit der Nacht ab: Was ist ueber Nacht schon erledigt und kann raus? "
        "Was ist GESCHEITERT und muss heute frueh nachgeholt werden?\n"
        "3. Sieh nach neuen Todos und Nachrichten (`list_todos`, `.agent_state.md`) — "
        "was ueber Nacht reinkam, gehoert in den Plan.\n"
        "4. Aendert sich etwas, schreibe den Plan mit `plan_day` neu (gleiche Regeln: "
        "Uhrzeit und mindestens 15 Minuten pro Block). Aendert sich nichts, lass ihn "
        "stehen und sag das.\n"
        "5. Danach faengst du mit dem ersten Block an — der Morgencheck ist kein "
        "Ersatz fuer Arbeit, nur ihr Anfang.\n"
    )


def rhythm_note(agent, now: datetime | None = None, *, spoken: bool = False) -> str:
    """Prompt-Block fuer JEDEN proaktiven Lauf: welche Phase, und was daraus folgt.

    Der Rhythmus-Zeitplan ist der Regelfall — dieser Block der Rueckfall. Faellt der
    Abendlauf aus (Agent war beschaeftigt, Container gestoppt), plant der naechste
    proaktive Lauf im Abendfenster trotzdem den naechsten Tag.

    ``spoken=True`` liefert dieselbe Lage fuer die Sprachfront — dort heisst das
    Werkzeug ``plan_my_day``, und der Agent soll die Planung abgeben statt sie
    vorzulesen.
    """
    now = now or datetime.now(timezone.utc)
    times = rhythm_times(agent)
    current = phase(agent, now)
    day = target_date(agent, now)
    head = (
        f"\n=== DEIN ARBEITSRHYTHMUS ===\n"
        f"Du planst taeglich um {times['evening']} den naechsten Tag und siehst um "
        f"{times['morning']} nochmal drueber ({times['timezone']}"
        + (", nur werktags" if times["weekdays_only"] else ", auch am Wochenende") + ").\n"
    )
    if spoken:
        # Die Stimme plant nicht selbst — sie gibt die Planung als Aufgabe ab. Wer hier
        # `plan_day` naehme, riefe ein Werkzeug auf, das es in diesem Kanal nicht gibt.
        if current == EVENING:
            body = (
                f"Es ist Feierabend-Zeit. Fragt der Nutzer nach deinem Tag oder nach "
                f"Planung, meint das den {day.isoformat()} — gib die Planung mit "
                "`plan_my_day` (horizon: \"tomorrow\") ab. Sag NIE, du haettest geplant, "
                "bevor das Werkzeug gelaufen ist."
            )
        elif current == MORNING:
            body = (
                "Es ist Morgen. Zeig auf Nachfrage mit `get_day_plan`, was heute ansteht, "
                "und sag dazu, was ueber Nacht gelaufen oder gescheitert ist. Soll sich "
                "etwas aendern, gib es mit `plan_my_day` ab."
            )
        else:
            body = (
                "Mitten am Tag: `get_day_plan` zeigt, was ansteht. Aenderungen gehen "
                "ueber `plan_my_day`, nicht durch Zusagen im Gespraech."
            )
    elif current == EVENING:
        body = (
            f"Es ist Feierabend-Zeit: plane JETZT den {day.isoformat()} mit `plan_day` "
            f"(`plan_date: \"{day.isoformat()}\"`), falls das heute noch nicht passiert "
            "ist — pruefe das mit `get_day_plan` fuer diesen Tag. Jeder Block mit Uhrzeit "
            "und mindestens 15 Minuten."
        )
    elif current == MORNING:
        body = (
            f"Es ist Morgen: sieh mit `get_day_plan` den Plan fuer heute durch, nimm "
            "auf, was ueber Nacht gelaufen oder gescheitert ist, schreibe ihn bei "
            "Bedarf mit `plan_day` neu — und fang dann mit dem ersten Block an."
        )
    else:
        body = (
            "Mitten am Tag: arbeite den Plan ab. Zieh vor, was du frueher schaffst, "
            "und halte Aenderungen mit `plan_day` nach, damit der Kalender stimmt."
        )
    return head + body + "\n=== ENDE ===\n"


# ---------------------------------------------------------------------------
# Takt eines Zeitplans in Worten (Kalender, Übersichten)
# ---------------------------------------------------------------------------

_WEEKDAY_NAMES = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"]


def describe_schedule(schedule) -> str:
    """Der Takt eines Zeitplans in einer lesbaren Zeile — „täglich 22:00", „alle 30 Min".

    Der Kalender zeigte geplante Laeufe nur als Uhrzeit plus Namen; ob dahinter ein
    taeglicher Rhythmus, ein Intervall oder ein Einmal-Lauf steckt, war nicht zu sehen.
    Genau das unterscheidet aber einen Plan-Block von einer stehenden Aufgabe.
    """
    cron = (schedule.cron_expression or "").strip()
    if cron:
        parts = cron.split()
        if len(parts) == 5:
            minute, hour, dom, mon, dow = parts
            if minute.isdigit() and hour.isdigit() and dom == "*" and mon == "*":
                zeit = f"{int(hour):02d}:{int(minute):02d}"
                if dow == "*":
                    return f"täglich {zeit}"
                if dow in ("1-5", "MON-FRI"):
                    return f"Mo–Fr {zeit}"
                tage = []
                for token in dow.replace("7", "0").split(","):
                    if token.isdigit() and 0 <= int(token) <= 6:
                        tage.append(_WEEKDAY_NAMES[int(token)])
                if tage:
                    return f"{', '.join(tage)} {zeit}"
        return f"Cron {cron}"
    seconds = schedule.interval_seconds or 0
    if seconds <= 0:
        return "einmalig"
    if seconds % 86400 == 0:
        return f"alle {seconds // 86400} Tage"
    if seconds % 3600 == 0:
        return f"alle {seconds // 3600} Std"
    return f"alle {max(seconds // 60, 1)} Min"


