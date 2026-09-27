"""Ein gerade geweckter Agent wird nicht gleich wieder schlafen gelegt.

Auf einer Anlage jede Stunde dasselbe Bild:

    12:00:04  [Aufwecken] Agent schlaeft — wird fuer die Zustellung gestartet
    12:00:04  [Scheduler] ... war gestoppt und wurde fuer den faelligen Lauf geweckt
    12:01:39  [UserLifecycle] Stopped agent ... after 15min inactivity

95 Sekunden nach dem Wecken. Die Abschaltung fragt, ob der NUTZER seit 15
Minuten nichts getan hat — und ob der Agent gerade arbeitet oder etwas in der
Warteschlange hat. Ein Agent, der gerade hochfaehrt, hat beides noch nicht:
er gilt als untaetig. Folge: Zeitplan-Laeufe wurden mitten im Start
abgeschossen, tauchten als "[Ausgefallen]" wieder auf und wurden erneut
gestartet — derselbe Kram, immer wieder.

Die Startzeit steht bereits bei Docker. Ein Container, der vor weniger als der
Leerlaufzeit gestartet wurde, ist nicht leerlaufend — er faengt gerade erst an.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import user_lifecycle as lifecycle

JETZT = datetime(2026, 9, 27, 10, 1, 39, tzinfo=timezone.utc)


def _docker(gestartet_vor_min: float | None):
    d = MagicMock()
    if gestartet_vor_min is None:
        d.get_container.side_effect = Exception("kein Container")
    else:
        # Genau das Format, das Docker liefert: Nanosekunden und "Z"
        start = (JETZT - timedelta(minutes=gestartet_vor_min)).strftime("%Y-%m-%dT%H:%M:%S") + ".662587871Z"
        d.get_container.return_value = SimpleNamespace(attrs={"State": {"StartedAt": start}})
    return d


# ---------------------------------------------------------------------------
# Die Entscheidung selbst
# ---------------------------------------------------------------------------

def test_vor_zwei_minuten_gestartet_ist_frisch():
    assert lifecycle._frisch_gestartet(_docker(1.6), "c1", JETZT, 15) is True


def test_vor_vierzig_minuten_gestartet_ist_nicht_frisch():
    assert lifecycle._frisch_gestartet(_docker(40), "c1", JETZT, 15) is False


def test_ohne_container_ist_nichts_frisch():
    """Im Zweifel greift die bisherige Regel — kein Freifahrtschein."""
    assert lifecycle._frisch_gestartet(_docker(None), "c1", JETZT, 15) is False
    assert lifecycle._frisch_gestartet(_docker(1), None, JETZT, 15) is False


def test_unlesbare_startzeit_ist_nicht_frisch():
    d = MagicMock()
    d.get_container.return_value = SimpleNamespace(attrs={"State": {"StartedAt": "0001-01-01T00:00:00Z"}})
    assert lifecycle._frisch_gestartet(d, "c1", JETZT, 15) is False


# ---------------------------------------------------------------------------
# Im Sweep
# ---------------------------------------------------------------------------

class _Ergebnis:
    def __init__(self, werte): self._w = werte
    def scalars(self): return self
    def all(self): return list(self._w)


class _Db:
    def __init__(self, agent, user):
        self._antworten = [[agent], [user]]
        self.commit = AsyncMock()
    async def execute(self, _q): return _Ergebnis(self._antworten.pop(0))
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


async def _sweep(gestartet_vor_min: float):
    agent = SimpleNamespace(id="2ad91565", name="Agent", user_id="u1", container_id="c1",
                            config={}, state=lifecycle.AgentState.RUNNING)
    user = SimpleNamespace(id="u1", email="nutzer@example.invalid", last_active_at=JETZT - timedelta(hours=3))
    svc = lifecycle.UserLifecycleService.__new__(lifecycle.UserLifecycleService)
    svc.db_factory = MagicMock()
    svc.docker = _docker(gestartet_vor_min)
    svc.redis = MagicMock()
    svc.redis.get_queue_depth = AsyncMock(return_value=0)
    svc.redis.get_agent_status = AsyncMock(return_value={"state": "idle"})

    class _Zeit(datetime):
        @classmethod
        def now(cls, tz=None): return JETZT

    with patch("app.db.session.resilient_session", lambda session_factory: _Db(agent, user)), \
         patch.object(lifecycle, "_get_timeout_minutes", AsyncMock(return_value=15)), \
         patch.object(lifecycle, "_has_imminent_schedule", AsyncMock(return_value=False)), \
         patch.object(lifecycle, "datetime", _Zeit):
        await svc._sweep()
    return svc.docker, agent


@pytest.mark.asyncio
async def test_frisch_geweckter_agent_wird_nicht_gestoppt():
    docker, agent = await _sweep(1.6)
    docker.stop_container.assert_not_called()
    assert agent.state == lifecycle.AgentState.RUNNING


@pytest.mark.asyncio
async def test_lange_laufender_untaetiger_agent_wird_weiter_gestoppt():
    """Die Abschaltung selbst bleibt — sie spart Speicher auf kleinen Geraeten."""
    docker, agent = await _sweep(40)
    docker.stop_container.assert_called_once_with("c1")
    assert agent.state == lifecycle.AgentState.STOPPED
