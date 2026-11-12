"""Ein Agent mit laufender Aufgabe wird nicht wegen Leerlauf gestoppt.

Auf einer Kundenanlage:

    18:02:34  Task ... is now running on agent ...
    18:08:09  Delegated task ... (completed) -> notified delegating agent ...
    18:09:37  [UserLifecycle] Stopped agent ... after 30min inactivity
    18:09:39  Verwaiste Aufgabe ... beendet (Agent ..., Container exited)

Der Agent hatte eine Aufgabe an den Pruef-Agenten delegiert. Als dessen
Ergebnis zurueckkam, stand sein Redis-Status kurz nicht auf "working". Die
Abschaltung sah nur Redis und stoppte ihn mitten im Lauf. Jetzt fragt sie
zusaetzlich die Aufgabentabelle.
"""

from unittest.mock import AsyncMock

import pytest

from app.services import user_lifecycle as lifecycle
from tests.test_lifecycle_frisch_geweckt import _sweep


@pytest.mark.asyncio
async def test_agent_mit_offener_aufgabe_wird_nicht_gestoppt():
    docker, agent = await _sweep(40, offene_aufgabe=True)
    docker.stop_container.assert_not_called()
    assert agent.state == lifecycle.AgentState.RUNNING


@pytest.mark.asyncio
async def test_agent_ohne_offene_aufgabe_wird_weiter_gestoppt():
    docker, _agent = await _sweep(40, offene_aufgabe=False)
    docker.stop_container.assert_called_once_with("c1")


class _Db:
    def __init__(self, treffer):
        self.treffer, self.abfrage = treffer, None
    async def scalar(self, q):
        self.abfrage = q
        return self.treffer


@pytest.mark.asyncio
@pytest.mark.parametrize("treffer, erwartet", [("t1", True), (None, False)])
async def test_offene_aufgabe_aus_der_datenbank(treffer, erwartet):
    db = _Db(treffer)
    assert await lifecycle._has_open_task(db, "a1") is erwartet
    sql = str(db.abfrage.compile(compile_kwargs={"literal_binds": True}))
    assert "a1" in sql and "NOT IN" in sql.upper()
    for endzustand in ("completed", "failed", "cancelled"):
        assert endzustand in sql.lower() or endzustand.upper() in sql


@pytest.mark.asyncio
async def test_datenbankfehler_laesst_agent_wach():
    db = AsyncMock()
    db.scalar.side_effect = RuntimeError("DB weg")
    assert await lifecycle._has_open_task(db, "a1") is True
