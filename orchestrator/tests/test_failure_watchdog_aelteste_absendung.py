"""Der Fehler-Waechter misst das Alter der AELTESTEN unquittierten Absendung.

Hintergrund (#988): `Schedule.last_run_at` wird beim ABSENDEN gesetzt
(`scheduler_service.py`, direkt nach dem Anlegen des Auftrags), nie beim
Fertigwerden. Solange ein Zeitplan ueberhaupt noch feuert, ist
`now - last_run_at` deshalb nie groesser als sein eigener Takt. Der frueher
dort gemessene Wert konnte die Karenzzeit von zwei Stunden bei jedem Takt
unterhalb dieser Grenze also NIE ueberschreiten — der Waechter war fuer die
Mehrzahl der getakteten Zeitplaene strukturell blind, gleich wie viele ihrer
Laeufe spurlos verschwanden.

Die Gegenprobe dazu steht in `test_30min_takt_war_vorher_blind`: derselbe
Aufbau mit der alten Bedingung bleibt stumm.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.task import is_terminal_task_status
from app.services.scheduler_service import _WATCHDOG_KARENZ


def _service(redis_mock):
    from app.services.scheduler_service import SchedulerService

    svc = SchedulerService.__new__(SchedulerService)
    svc.redis = redis_mock
    svc._failure_watchdog_last_run = None
    svc._watchdog_alerted = {}
    svc._watchdog_gemeldet_um = {}
    return svc


def _redis():
    redis = MagicMock()
    redis.client = AsyncMock()
    redis.client.publish = AsyncMock()
    return redis


def _schnell_getakteter_zeitplan(now, *, total=10, ok=7, fail=1):
    """30-Minuten-Takt, zuletzt vor 5 Minuten gefeuert, drift=2."""
    from app.models.schedule import Schedule

    return Schedule(
        id="sched-schnell",
        name="PR- und Issue-Bearbeiter",
        prompt="x",
        interval_seconds=1800,
        next_run_at=now + timedelta(minutes=25),
        last_run_at=now - timedelta(minutes=5),
        total_runs=total,
        success_count=ok,
        fail_count=fail,
    )


def _offene_aufgabe(schedule_id, erstellt):
    from app.models.task import Task, TaskStatus

    return Task(
        id="task-haengt",
        title="[Scheduled] PR- und Issue-Bearbeiter",
        prompt="x",
        status=TaskStatus.RUNNING,
        created_at=erstellt,
        metadata_={"schedule_id": schedule_id},
    )


def _db_ctx(schedules, tasks, jetzt=None):
    """Antwortet je nach ABGEFRAGTER Tabelle, nicht nach Aufrufreihenfolge.

    Gefiltert wird mit denselben zwei Bedingungen wie in der echten Abfrage
    (Status nicht endgueltig, aelter als die Karenzzeit) — eine Attrappe, die
    die WHERE-Klausel ignoriert, bleibt auch dann gruen, wenn der Filter im
    Produktionscode ersatzlos verschwindet.

    `resilient_session` schickt vor dem Koerper ein `SELECT 1` als Vorab-Ping;
    eine Reihenfolgen-Attrappe (`side_effect=[...]`) gaebe die Zeitplaene
    deshalb an den Ping und die Aufgaben an die Zeitplan-Abfrage — der Test
    waere rot aus einem Grund, der mit dem Pruefgegenstand nichts zu tun hat.
    """

    jetzt = jetzt or datetime.now(timezone.utc)

    def _antwort(statement, *args, **kwargs):
        text = " ".join(str(statement).split())
        res = MagicMock()
        if "FROM tasks" in text:
            # Die Attrappe fuehrt die WHERE-Klausel selbst aus, statt sie zu
            # ignorieren: sonst bliebe die Suite auch dann gruen, wenn der
            # Statusfilter ersatzlos verschwindet — und genau der haelt
            # abgeschlossene Laeufe aus der Messung heraus.
            assert "status NOT IN" in text, (
                "Die Abfrage filtert nicht mehr auf nicht-endgueltige Zustaende — "
                "abgeschlossene Aufgaben wuerden als Verlust gemeldet."
            )
            assert "created_at <" in text, (
                "Die Abfrage ist nicht mehr zeitlich begrenzt — sie laedt dann "
                "auch jede frisch abgesandte Aufgabe."
            )
            # Beide Filter der echten Abfrage, sonst prueft der Test einen
            # Pfad, den die Produktion gar nicht nimmt.
            grenze = jetzt - _WATCHDOG_KARENZ
            gefiltert = [
                t
                for t in tasks
                if not is_terminal_task_status(t.status) and t.created_at < grenze
            ]
            # Spaltenauswahl: der Produktionscode liest Zeilen als
            # (created_at, metadata_), nicht als Task-Objekte.
            res.all.return_value = [(t.created_at, t.metadata_) for t in gefiltert]
            return res
        menge = schedules if "FROM schedules" in text else []
        res.scalars.return_value.all.return_value = menge
        return res

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=_antwort)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_db)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.asyncio
async def test_meldet_verlust_bei_schnellem_takt():
    """30-Min-Takt, Absendung haengt seit 4 h ohne Endstatus -> Alarm."""
    now = datetime.now(timezone.utc)
    redis = _redis()
    svc = _service(redis)
    zeitplan = _schnell_getakteter_zeitplan(now)
    aufgabe = _offene_aufgabe(zeitplan.id, now - timedelta(hours=4))

    with patch(
        "app.db.session.async_session_factory",
        return_value=_db_ctx([zeitplan], [aufgabe]),
    ):
        await svc._tick_failure_watchdog()

    redis.client.publish.assert_awaited_once()
    assert svc._watchdog_alerted["sched-schnell"] == 2


@pytest.mark.asyncio
async def test_30min_takt_war_vorher_blind():
    """GEGENPROBE am echten Pfad: mit dem alten Mass bleibt derselbe Aufbau stumm.

    Hier wird nicht nachgerechnet, sondern `_tick_failure_watchdog` wirklich
    ausgefuehrt — nur ohne die neue Messquelle (der Helfer liefert nichts,
    genau wie es vor #988 war, als es ihn nicht gab). Bleibt der Waechter dann
    still und mit Messquelle laut, hat die ersetzte Zeile den Unterschied
    gemacht und nicht der Testaufbau.
    """
    now = datetime.now(timezone.utc)
    redis = _redis()
    svc = _service(redis)
    zeitplan = _schnell_getakteter_zeitplan(now)
    aufgabe = _offene_aufgabe(zeitplan.id, now - timedelta(hours=4))

    async def _keine_messquelle(db, jetzt):
        return {}

    svc._aelteste_offene_absendung = _keine_messquelle
    with patch(
        "app.db.session.async_session_factory",
        return_value=_db_ctx([zeitplan], [aufgabe]),
    ):
        await svc._tick_failure_watchdog()

    redis.client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_abgeschlossene_aufgabe_ist_kein_verlust():
    """Ein beendeter Lauf desselben Zeitplans darf nicht als offen zaehlen.

    Ohne den Statusfilter waere jede alte, laengst fertige Aufgabe ein
    Dauer-Alarmgrund — der haeufigste Weg, aus einem Waechter einen
    Stoerenfried zu machen, den der Nutzer abschaltet.
    """
    from app.models.task import TaskStatus

    now = datetime.now(timezone.utc)
    redis = _redis()
    svc = _service(redis)
    zeitplan = _schnell_getakteter_zeitplan(now)
    fertig = _offene_aufgabe(zeitplan.id, now - timedelta(hours=9))
    fertig.status = TaskStatus.COMPLETED

    with patch(
        "app.db.session.async_session_factory",
        return_value=_db_ctx([zeitplan], [fertig]),
    ):
        await svc._tick_failure_watchdog()

    redis.client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_junge_absendung_kann_den_alarm_nicht_unterdruecken():
    """Eine JUENGERE offene Absendung darf das alte Mass nicht ersetzen.

    Die Abfrage siebt junge Absendungen heute schon an der Datenbank aus, der
    Helfer sollte also gar keine liefern. `max(...)` statt "neu ersetzt alt"
    ist die zweite Sicherung fuer den Fall, dass sich das aendert — und genau
    die wird hier geprueft, indem der Helfer einen jungen Wert liefert.

    Warum das keine Erfindung ist: eine Selbstheilungs-Wiederholung erbt die
    `schedule_id` und traegt ein frisches `created_at`, ein Probelauf von Hand
    ebenso. Faellt die Zeitgrenze der Abfrage jemals weg, wuerde ausgerechnet
    so eine junge Zeile den Waechter dort stummschalten, wo er vor #988 als
    einziges funktionierte.
    """
    from app.models.schedule import Schedule

    now = datetime.now(timezone.utc)
    redis = _redis()
    svc = _service(redis)
    zeitplan = Schedule(
        id="sched-taeglich",
        name="Taeglicher Bericht",
        prompt="x",
        interval_seconds=86400,
        next_run_at=now + timedelta(days=1),
        last_run_at=now - timedelta(hours=6),
        total_runs=10,
        success_count=6,
        fail_count=1,
    )

    async def _liefert_junge_absendung(db, jetzt):
        return {zeitplan.id: jetzt - timedelta(minutes=4)}

    svc._aelteste_offene_absendung = _liefert_junge_absendung
    with patch(
        "app.db.session.async_session_factory",
        return_value=_db_ctx([zeitplan], [], jetzt=now),
    ):
        await svc._tick_failure_watchdog()

    redis.client.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_meldepause_bremst_die_stuendliche_flut():
    """Waechst `drift` bei jedem Takt, darf trotzdem nicht stuendlich gemeldet werden.

    Die Drift-Dedup allein greift im Zielfall nie: ein Zeitplan, der weiter
    feuert und nichts quittiert, erhoeht `drift` mit jedem Lauf. Vor #988 fiel
    das nicht auf, weil die Zeitbedingung solche Zeitplaene ohnehin
    aussortierte — seitdem braucht es die zweite Bremse.
    """
    now = datetime.now(timezone.utc)
    redis = _redis()
    svc = _service(redis)

    for stunde in range(5):
        zeitplan = _schnell_getakteter_zeitplan(
            now, total=10 + 2 * stunde, ok=7, fail=1
        )
        aufgabe = _offene_aufgabe(zeitplan.id, now - timedelta(hours=4))
        svc._failure_watchdog_last_run = None  # der stuendliche Takt ist faellig
        with patch(
            "app.db.session.async_session_factory",
            return_value=_db_ctx([zeitplan], [aufgabe]),
        ):
            await svc._tick_failure_watchdog()

    assert redis.client.publish.await_count == 1, (
        "Fuenf Durchgaenge mit wachsender Drift duerfen genau EINE Meldung "
        f"ergeben, nicht {redis.client.publish.await_count}."
    )


@pytest.mark.asyncio
async def test_junge_offene_absendung_meldet_nicht():
    """Der Daempfer bleibt: eine Absendung in der Karenzzeit ist kein Verlust.

    Dieser Test unterscheidet NICHT zwischen altem und neuem Mass — bei einem
    30-Minuten-Takt sind beide klein. Er sichert nur zu, dass die Verschaerfung
    den Daempfer nicht mitgenommen hat.
    """
    now = datetime.now(timezone.utc)
    redis = _redis()
    svc = _service(redis)
    zeitplan = _schnell_getakteter_zeitplan(now)
    aufgabe = _offene_aufgabe(zeitplan.id, now - timedelta(minutes=35))

    with patch(
        "app.db.session.async_session_factory",
        return_value=_db_ctx([zeitplan], [aufgabe]),
    ):
        await svc._tick_failure_watchdog()

    redis.client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_rueckfall_auf_last_run_at_ohne_offene_aufgabe():
    """Findet sich keine offene Absendung, gilt das alte Mass weiter.

    Damit ist der Waechter nie unempfindlicher als vorher — der Fall "Aufgabe
    geloescht / Verlust aelter als diese Version" muss weiter melden.
    """
    from app.models.schedule import Schedule

    now = datetime.now(timezone.utc)
    redis = _redis()
    svc = _service(redis)
    zeitplan = Schedule(
        id="sched-taeglich",
        name="Taeglicher Bericht",
        prompt="x",
        interval_seconds=86400,
        next_run_at=now + timedelta(days=1),
        last_run_at=now - timedelta(hours=5),
        total_runs=10,
        success_count=7,
        fail_count=1,
    )

    with patch(
        "app.db.session.async_session_factory",
        return_value=_db_ctx([zeitplan], []),
    ):
        await svc._tick_failure_watchdog()

    redis.client.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_fremde_aufgabe_zaehlt_nicht_fuer_diesen_zeitplan():
    """Die offene Absendung eines ANDEREN Zeitplans darf nicht durchschlagen."""
    now = datetime.now(timezone.utc)
    redis = _redis()
    svc = _service(redis)
    zeitplan = _schnell_getakteter_zeitplan(now)
    fremd = _offene_aufgabe("sched-ganz-anders", now - timedelta(hours=9))

    with patch(
        "app.db.session.async_session_factory",
        return_value=_db_ctx([zeitplan], [fremd]),
    ):
        await svc._tick_failure_watchdog()

    redis.client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_aelteste_gewinnt_gegen_juengste():
    """Zwei offene Absendungen desselben Zeitplans: die AELTESTE entscheidet.

    Nimmt der Helfer versehentlich die juengste, ist der Waechter wieder genau
    so blind wie mit `last_run_at` — nur eine Tabelle weiter.
    """
    now = datetime.now(timezone.utc)
    redis = _redis()
    svc = _service(redis)
    zeitplan = _schnell_getakteter_zeitplan(now)
    alt = _offene_aufgabe(zeitplan.id, now - timedelta(hours=6))
    alt.id = "task-alt"
    jung = _offene_aufgabe(zeitplan.id, now - timedelta(minutes=3))
    jung.id = "task-jung"

    # Beide Reihenfolgen, damit der Test nicht zufaellig an der Listenfolge haengt.
    for reihenfolge in ([alt, jung], [jung, alt]):
        redis.client.publish.reset_mock()
        svc._watchdog_alerted = {}
        svc._watchdog_gemeldet_um = {}
        svc._failure_watchdog_last_run = None
        with patch(
            "app.db.session.async_session_factory",
            return_value=_db_ctx([zeitplan], reihenfolge),
        ):
            await svc._tick_failure_watchdog()
        redis.client.publish.assert_awaited_once()


def test_terminal_status_haelt_den_erwarteten_satz():
    """Welche Zustaende endgueltig sind, steht hier AUSGESCHRIEBEN.

    Ein Vergleich von `TERMINAL_TASK_STATUSES` gegen `is_terminal_task_status`
    waere wertlos: die Funktion liest seit #988 dieselbe Konstante, der
    Vergleich ist damit immer wahr (nachgewiesen — eine Mutation, die
    CANCELLED aus der Konstante entfernt, blieb gruen). Erst der hier
    aufgeschriebene Satz faengt sie, denn die Waechter-Abfrage haengt daran:
    faellt ein Zustand heraus, gelten laufende Aufgaben als offen und der
    Waechter meldet Verluste, die keine sind.
    """
    from app.models.task import (
        TERMINAL_TASK_STATUSES,
        TaskStatus,
        is_terminal_task_status,
    )

    assert set(TERMINAL_TASK_STATUSES) == {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
    assert set(TaskStatus) - set(TERMINAL_TASK_STATUSES) == {
        TaskStatus.PENDING,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
    }
    assert is_terminal_task_status(TaskStatus.CANCELLED)
    assert not is_terminal_task_status(TaskStatus.RUNNING)


# ---------------------------------------------------------------------------
# Buchhaltung: ein als stale beendeter Lauf gehoert auf den Zeitplan gebucht
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_lauf_wird_auf_dem_zeitplan_gebucht():
    """`mark_task_stale` geht an `_update_schedule_stats` vorbei — die Zaehler
    des Zeitplans muessen trotzdem schliessen.

    Ohne diese Buchung bleibt `drift` fuer immer erhoeht (die Aufgabe ist
    endgueltig, also nie wieder "offen"), und der Fehler-Waechter meldet
    denselben, laengst beendeten Lauf erneut. Seit #988 kommt dazu: die
    stale-markierte Aufgabe faellt aus der Alters-Messung heraus, der Verlust
    waere danach unsichtbar.
    """
    from app.models.schedule import Schedule
    from app.services.scheduler_service import SchedulerService

    now = datetime.now(timezone.utc)
    zeitplan = Schedule(
        id="sched-schnell",
        name="PR- und Issue-Bearbeiter",
        prompt="x",
        interval_seconds=1800,
        next_run_at=now,
        last_run_at=now,
        total_runs=10,
        success_count=7,
        fail_count=1,
        consecutive_failures=3,
    )
    aufgabe = _offene_aufgabe(zeitplan.id, now - timedelta(hours=4))

    mock_db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = zeitplan
    mock_db.execute = AsyncMock(return_value=res)

    svc = SchedulerService.__new__(SchedulerService)
    await svc._buche_stale_auf_zeitplan(mock_db, aufgabe)

    assert zeitplan.fail_count == 2, "Der Verlust wurde nicht gebucht."
    assert zeitplan.consecutive_failures == 3, (
        "`consecutive_failures` haengt an der automatischen Abschaltung und "
        "darf von einem haengenden Lauf nicht hochgezaehlt werden."
    )


@pytest.mark.asyncio
async def test_stale_buchung_ignoriert_aufgaben_ohne_zeitplan():
    """Eine Chat- oder Hand-Aufgabe hat keinen Zeitplan — und darf keinen treffen."""
    from app.models.task import Task, TaskStatus
    from app.services.scheduler_service import SchedulerService

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=AssertionError("darf nicht abfragen"))
    aufgabe = Task(
        id="task-frei",
        title="Handarbeit",
        prompt="x",
        status=TaskStatus.RUNNING,
        metadata_={},
    )

    svc = SchedulerService.__new__(SchedulerService)
    await svc._buche_stale_auf_zeitplan(mock_db, aufgabe)
    mock_db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_waechter_ruft_die_buchung_wirklich_auf():
    """Die Buchung muss im Stale-Waechter VERDRAHTET sein, nicht nur existieren.

    Ein Test, der `_buche_stale_auf_zeitplan` direkt aufruft, bleibt gruen,
    wenn der Aufruf aus `_tick_stale_task_watchdog` ersatzlos verschwindet —
    nachgewiesen mit genau dieser Mutation. Deshalb hier der ganze Durchgang.
    """
    from app.models.schedule import Schedule
    from app.services.scheduler_service import SchedulerService

    now = datetime.now(timezone.utc)
    zeitplan = Schedule(
        id="sched-schnell",
        name="PR- und Issue-Bearbeiter",
        prompt="x",
        interval_seconds=1800,
        next_run_at=now,
        last_run_at=now,
        total_runs=10,
        success_count=7,
        fail_count=1,
    )
    aufgabe = _offene_aufgabe(zeitplan.id, now - timedelta(hours=4))
    aufgabe.agent_id = "agent-1"

    mock_db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = zeitplan
    res.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=res)
    mock_db.add = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_db)
    cm.__aexit__ = AsyncMock(return_value=False)

    svc = SchedulerService.__new__(SchedulerService)
    svc.docker = None
    svc.redis = None

    with patch("app.db.session.async_session_factory", return_value=cm), patch(
        "app.services.scheduler_service.find_stale_tasks",
        new=AsyncMock(return_value=[aufgabe]),
    ):
        await svc._tick_stale_task_watchdog()

    from app.models.task import TaskStatus

    assert aufgabe.status == TaskStatus.FAILED, "Die Aufgabe wurde nicht beendet."
    assert zeitplan.fail_count == 2, (
        "Der Stale-Waechter hat den Verlust nicht auf dem Zeitplan gebucht — "
        "`drift` bliebe damit fuer immer erhoeht."
    )
