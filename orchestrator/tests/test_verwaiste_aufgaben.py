"""Verwaiste Aufgaben: der Container ist neu, der Lauf darin ist weg.

Wird ein Agenten-Container neu erstellt (Aktualisieren, Neustart) oder stirbt er
und Docker startet ihn neu, endet jeder Lauf, der darin steckte. Die Zeile in der
Datenbank erfaehrt davon nichts und steht weiter auf ``running`` — bis der
Stillstands-Waechter nach ``watchdog_stale_task_minutes`` (Standard 180)
zuschlaegt. Bis dahin sieht der Nutzer eine Aufgabe „laufen", die es nicht mehr
gibt, und der Agent gilt als beschaeftigt.

Der Beweis kommt von Docker, nicht aus Redis: Ist der Container NACH dem Start
der Aufgabe gestartet worden (oder laeuft er gar nicht mehr), kann der Prozess,
der die Aufgabe bearbeitet hat, nicht mehr existieren. Der Redis-Status taugt
dafuer nicht — er ueberlebt den Container und nennt danach Aufgaben, die
niemand mehr bearbeitet.

Umgekehrt darf nichts abgebrochen werden, das noch laeuft: nach einem reinen
Orchestrator-Neustart arbeitet der Container weiter, und eine Aufgabe, die erst
im NEUEN Container begonnen hat, ist putzmunter.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from docker.errors import APIError, NotFound
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core.task_router import TaskRouter
from app.models.agent import Agent, AgentState
from app.models.approval_rule import ApprovalRule
from app.models.command_approval import CommandApproval
from app.models.job_state import JobState
from app.models.notification import Notification
from app.models.task import Task, TaskStatus
from app.models.task_rating import TaskRating
from app.models.task_step import TaskStep
from app.services.docker_service import DockerService
from app.services.watchdog import docker_startzeit, lauf_nachweislich_weg


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


UTC = timezone.utc
JETZT = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def _docker_zeit(dt: datetime) -> str:
    """So liefert Docker ``State.StartedAt``: Nanosekunden und ``Z``."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "123Z"


# ── Die Entscheidung allein ──────────────────────────────────────────────────


class LaufNachweislichWeg(unittest.TestCase):
    def test_container_nach_aufgabenstart_neu_gestartet(self):
        self.assertTrue(lauf_nachweislich_weg(
            JETZT - timedelta(minutes=5), "running", JETZT - timedelta(minutes=1)))

    def test_aufgabe_erst_im_neuen_container_begonnen(self):
        """Der Fall nach einem reinen Orchestrator-Neustart — oder eine Aufgabe,
        die der frische Container schon aufgenommen hat."""
        self.assertFalse(lauf_nachweislich_weg(
            JETZT - timedelta(minutes=1), "running", JETZT - timedelta(minutes=5)))

    def test_unlesbare_startzeit_ist_kein_beweis(self):
        self.assertFalse(lauf_nachweislich_weg(JETZT, "running", None))

    def test_unbekannter_zustand_ist_kein_beweis(self):
        """Docker nicht erreichbar heisst nicht „Container weg"."""
        self.assertFalse(lauf_nachweislich_weg(JETZT, "unbekannt", None))

    def test_angehaltener_container_lebt_noch(self):
        self.assertFalse(lauf_nachweislich_weg(JETZT, "paused", None))

    def test_container_ohne_laufenden_prozess(self):
        for zustand in ("fehlt", "exited", "dead", "created", "restarting", "removing"):
            with self.subTest(zustand=zustand):
                self.assertTrue(lauf_nachweislich_weg(JETZT, zustand, None))

    def test_aufgabe_ohne_startzeit_im_laufenden_container(self):
        """Ohne Startzeit ist kein Vergleich moeglich — dann nichts anfassen."""
        self.assertFalse(lauf_nachweislich_weg(None, "running", JETZT))

    def test_naive_zeitstempel_gelten_als_utc(self):
        self.assertTrue(lauf_nachweislich_weg(
            (JETZT - timedelta(minutes=5)).replace(tzinfo=None), "running", JETZT))


class DockerStartzeit(unittest.TestCase):
    def test_nanosekunden_werden_gelesen(self):
        self.assertEqual(docker_startzeit("2026-10-03T12:00:01.123456789Z"),
                         datetime(2026, 10, 3, 12, 0, 1, 123456, tzinfo=UTC))

    def test_nie_gestartet(self):
        self.assertIsNone(docker_startzeit("0001-01-01T00:00:00Z"))

    def test_unlesbar(self):
        self.assertIsNone(docker_startzeit("kaputt"))
        self.assertIsNone(docker_startzeit(None))


# ── Docker befragen ──────────────────────────────────────────────────────────


def _container(cid, name, status, start):
    return SimpleNamespace(id=cid, name=name, status=status,
                           attrs={"State": {"StartedAt": start}})


class AgentContainerZustand(unittest.TestCase):
    def _docker(self, get=None, liste=()):
        d = DockerService.__new__(DockerService)
        d.client = MagicMock()
        d.client.containers.get.side_effect = get
        d.client.containers.list.return_value = list(liste)
        return d

    def test_vorhandener_container(self):
        c = _container("c1", "ai-agent-x-a1", "running", _docker_zeit(JETZT))
        d = self._docker(get=lambda ref: c)
        zustand, start = d.agent_container_zustand("c1", "a1")
        self.assertEqual(zustand, "running")
        self.assertEqual(start.replace(microsecond=0), JETZT)

    def test_alte_kennung_weg_neuer_container_unter_festem_namen(self):
        """Zwischen Neubau und Speichern der neuen Kennung steht noch die alte in
        der Datenbank. Der neue Container ist trotzdem da — und darf nicht als
        „fehlt" gelten, sonst stirbt eine Aufgabe, die er schon aufgenommen hat."""
        neu = _container("c2", "ai-agent-x-a1", "running", _docker_zeit(JETZT))
        fremd = _container("c9", "ai-agent-y-a11", "running", _docker_zeit(JETZT))

        def get(ref):
            if ref == "c1":
                raise NotFound("weg")
            return {"c2": neu, "c9": fremd}[ref]

        d = self._docker(get=get, liste=[fremd, neu])
        self.assertEqual(d.agent_container_zustand("c1", "a1")[0], "running")

    def test_kein_container_mehr(self):
        def get(ref):
            raise NotFound("weg")

        d = self._docker(get=get, liste=[])
        self.assertEqual(d.agent_container_zustand("c1", "a1"), ("fehlt", None))

    def test_docker_nicht_erreichbar(self):
        def get(ref):
            raise APIError("Daemon weg")

        d = self._docker(get=get)
        self.assertEqual(d.agent_container_zustand("c1", "a1"), ("unbekannt", None))


# ── Von Ende zu Ende gegen echtes SQL ────────────────────────────────────────


class _FakeRedisClient:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel, payload):  # noqa: ANN001
        self.published.append((channel, payload))

    async def rpush(self, *a, **kw):  # noqa: ANN001, D102
        return 1

    async def ltrim(self, *a, **kw):  # noqa: ANN001, D102
        return True

    async def lpush(self, *a, **kw):  # noqa: ANN001, D102
        return 1

    async def lrange(self, *a, **kw):  # noqa: ANN001, D102
        return []


class _FakeRedis:
    def __init__(self):
        self.client = _FakeRedisClient()

    async def push_task(self, agent_id, payload):  # noqa: ANN001, D102
        return None


class _FakeLoadBalancer:
    async def select_agent(self, priority=1):  # noqa: ANN001
        return None


class _FakeDocker:
    """Antwortet je Agent mit (Zustand, Startzeit) — wie ``agent_container_zustand``."""

    def __init__(self, zustaende: dict[str, tuple[str, datetime | None]]):
        self.zustaende = zustaende
        self.gefragt: list[str] = []

    def agent_container_zustand(self, container_id, agent_id):  # noqa: ANN001
        self.gefragt.append(agent_id)
        return self.zustaende.get(agent_id, ("unbekannt", None))


class _Basis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, Task, Notification, CommandApproval, ApprovalRule,
                          TaskRating, TaskStep, JobState):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.redis = _FakeRedis()
        self.jetzt = datetime.now(UTC)

        async with self.Session() as db:
            db.add(Agent(id="a1", name="Recherche", state=AgentState.RUNNING,
                         container_id="c1", user_id="u1", config={}))
            db.add(Agent(id="a2", name="Buchhaltung", state=AgentState.RUNNING,
                         container_id="c2", user_id="u2", config={}))
            db.add(Agent(id="a3", name="Gestoppt", state=AgentState.STOPPED,
                         container_id="c3", user_id="u1", config={}))
            # Lief schon, bevor der Container neu kam — verwaist.
            db.add(self._laufend("alt", "a1", minuten=10))
            # Hat erst der neue Container aufgenommen — lebt.
            db.add(self._laufend("neu", "a1", minuten=1))
            # Fremder Agent, dessen Container nur der Orchestrator-Neustart nicht
            # beruehrt hat — lebt.
            db.add(self._laufend("fremd", "a2", minuten=10))
            db.add(self._laufend("gestoppt", "a3", minuten=10))
            db.add(Task(id="fertig", title="fertig", prompt="x", agent_id="a1",
                        status=TaskStatus.COMPLETED,
                        started_at=self.jetzt - timedelta(minutes=10)))
            db.add(JobState(id="task:alt", kind="agent_task", ref_id="alt",
                            status="running", last_heartbeat=self.jetzt,
                            job_metadata={"agent_id": "a1", "prompt": "x"}))
            await db.commit()

        # a1: Container vor drei Minuten neu gestartet. a2: laeuft seit einer Stunde.
        # a3: gestoppt.
        self.docker = _FakeDocker({
            "a1": ("running", self.jetzt - timedelta(minutes=3)),
            "a2": ("running", self.jetzt - timedelta(hours=1)),
            "a3": ("exited", None),
        })

    def _laufend(self, tid, agent_id, *, minuten):
        return Task(id=tid, title=f"Aufgabe {tid}", prompt=f"Tu {tid}.", agent_id=agent_id,
                    status=TaskStatus.RUNNING, model="claude-sonnet-5", metadata_={},
                    started_at=self.jetzt - timedelta(minutes=minuten))

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _aufgabe(self, tid):
        async with self.Session() as db:
            return await db.get(Task, tid)

    async def _wiederholungen(self, von):
        async with self.Session() as db:
            rows = (await db.execute(select(Task))).scalars().all()
            return [t for t in rows if (t.metadata_ or {}).get("heal_of") == von]


class VerwaisteLaeufeBeenden(_Basis):
    async def _fegen(self, agent_id=None):
        async with self.Session() as db:
            router = TaskRouter(db, self.redis, _FakeLoadBalancer(), docker_service=self.docker)
            return await router.beende_verwaiste_laeufe(agent_id)

    async def test_haengende_aufgabe_nach_neustart_bekommt_endstatus(self):
        beendet = await self._fegen()
        alt = await self._aufgabe("alt")
        self.assertEqual(alt.status, TaskStatus.FAILED)
        self.assertIn("neu gestartet", alt.error)
        self.assertIsNotNone(alt.completed_at)
        self.assertEqual(beendet, 2, "alt (a1) und gestoppt (a3)")

    async def test_unterbrochene_aufgabe_wird_neu_eingereiht(self):
        """Der Abbruch ist nicht ihre Schuld — die Selbstheilung reiht sie neu ein."""
        await self._fegen()
        wiederholungen = await self._wiederholungen("alt")
        self.assertEqual(len(wiederholungen), 1)
        self.assertEqual(wiederholungen[0].agent_id, "a1")
        self.assertEqual(wiederholungen[0].status, TaskStatus.PENDING)
        self.assertEqual(wiederholungen[0].prompt, "Tu alt.")

    async def test_checkpoint_verschwindet_damit_der_naechste_start_nicht_doppelt_fortsetzt(self):
        await self._fegen()
        async with self.Session() as db:
            self.assertIsNone(await db.get(JobState, "task:alt"))

    async def test_aufgabe_im_neuen_container_laeuft_weiter(self):
        await self._fegen()
        self.assertEqual((await self._aufgabe("neu")).status, TaskStatus.RUNNING)

    async def test_fremder_agent_bleibt_unberuehrt(self):
        await self._fegen()
        self.assertEqual((await self._aufgabe("fremd")).status, TaskStatus.RUNNING)
        self.assertEqual(await self._wiederholungen("fremd"), [])

    async def test_beschraenkt_auf_einen_agenten(self):
        """Der Neubau von a1 fasst die Aufgaben von a3 nicht an — auch wenn
        deren Container ebenfalls nicht laeuft."""
        beendet = await self._fegen("a1")
        self.assertEqual(beendet, 1)
        self.assertEqual((await self._aufgabe("gestoppt")).status, TaskStatus.RUNNING)
        self.assertEqual(self.docker.gefragt, ["a1"])

    async def test_gestoppter_agent_wird_nicht_wieder_geweckt(self):
        """Wer einen Agenten anhaelt, will nicht, dass er 60 Sekunden spaeter fuer
        eine Wiederholung wieder hochfaehrt. Endstatus ja, neuer Versuch nein."""
        await self._fegen()
        gestoppt = await self._aufgabe("gestoppt")
        self.assertEqual(gestoppt.status, TaskStatus.FAILED)
        self.assertIn("läuft nicht mehr", gestoppt.error)
        self.assertEqual(await self._wiederholungen("gestoppt"), [])

    async def test_fertige_aufgabe_bleibt_fertig(self):
        await self._fegen()
        self.assertEqual((await self._aufgabe("fertig")).status, TaskStatus.COMPLETED)

    async def test_docker_unbekannt_bricht_nichts_ab(self):
        self.docker.zustaende = {}
        self.assertEqual(await self._fegen(), 0)
        for tid in ("alt", "neu", "fremd", "gestoppt"):
            self.assertEqual((await self._aufgabe(tid)).status, TaskStatus.RUNNING)

    async def test_ohne_docker_nichts_tun(self):
        async with self.Session() as db:
            router = TaskRouter(db, self.redis, _FakeLoadBalancer())
            self.assertEqual(await router.beende_verwaiste_laeufe(), 0)
        self.assertEqual((await self._aufgabe("alt")).status, TaskStatus.RUNNING)

    async def test_zweiter_durchlauf_findet_nichts_mehr(self):
        await self._fegen()
        self.assertEqual(await self._fegen(), 0)


class OrchestratorStart(_Basis):
    """``recover_stale_tasks`` laeuft beim Start. Bisher fasste er nur Aufgaben an,
    die aelter als zehn Minuten waren UND die der Redis-Status nicht mehr nannte —
    der Status ueberlebt aber den Container und nannte sie weiter."""

    async def test_start_beendet_verwaiste_auch_wenn_redis_sie_noch_nennt(self):
        # Der Redis-Status von a1 stammt noch aus dem alten Container.
        status = {
            "a1": {"state": "working", "current_task": "alt",
                   "active_sessions": '["alt", "neu"]'},
            "a2": {"state": "working", "current_task": "fremd"},
        }
        self.redis.get_agent_status = AsyncMock(side_effect=lambda aid: status.get(aid, {}))
        self.redis.get_queue_depth = AsyncMock(return_value=0)
        async with self.Session() as db:
            router = TaskRouter(db, self.redis, _FakeLoadBalancer(), docker_service=self.docker)
            await router.recover_stale_tasks(stale_minutes=10)
        self.assertEqual((await self._aufgabe("alt")).status, TaskStatus.FAILED)
        self.assertEqual((await self._aufgabe("neu")).status, TaskStatus.RUNNING)
        self.assertEqual((await self._aufgabe("fremd")).status, TaskStatus.RUNNING)


class WaechterTakt(_Basis):
    """Stirbt der Container und Docker startet ihn neu, ruft niemand
    ``update_agent`` — dann faengt es der Takt des Stillstands-Waechters, ohne
    erst ``watchdog_stale_task_minutes`` abzuwarten."""

    async def test_takt_beendet_verwaiste_ohne_auf_die_schwelle_zu_warten(self):
        from app.services import scheduler_service
        from app.services.scheduler_service import SchedulerService

        svc = SchedulerService(redis=self.redis, docker_service=self.docker)
        with patch.object(scheduler_service, "resilient_session", self.Session):
            await svc._tick_stale_task_watchdog()
        self.assertEqual((await self._aufgabe("alt")).status, TaskStatus.FAILED)
        self.assertEqual((await self._aufgabe("neu")).status, TaskStatus.RUNNING)
        self.assertEqual((await self._aufgabe("fremd")).status, TaskStatus.RUNNING)


class NeubauDesContainers(_Basis):
    """``update_agent`` und ``restart_agent`` raeumen sofort auf — nicht erst
    beim naechsten Takt des Waechters."""

    def _manager(self, db, weg):
        from app.core.agent_manager import AgentManager

        docker = MagicMock()
        docker.create_container.return_value = SimpleNamespace(id="c1-neu")
        # Nach dem Neubau: frischer Container, gestartet vor wenigen Sekunden.
        docker.agent_container_zustand.side_effect = lambda cid, aid: (
            ("running", datetime.now(UTC)) if aid == "a1" else ("unbekannt", None)
        )
        mgr = AgentManager(db, docker, self.redis)
        async_stubs = {
            "_agent_redis_url": "redis://x", "_build_tools_aktiv": False,
            "_get_secrets_env": {}, "_effective_llm_config": None,
            "_owner_credential_env": {}, "_get_custom_mcp_env": {},
            "_get_integration_env": {}, "migrate_knowledge_file": True,
        }
        patches = [patch.object(mgr, n, AsyncMock(return_value=v)) for n, v in async_stubs.items()]
        patches += [
            patch.object(mgr, "_build_provider_env", MagicMock(return_value={})),
            patch.object(mgr, "_cli_account_env", MagicMock(return_value={})),
            patch.object(mgr, "_apply_permissions", MagicMock()),
            patch.object(mgr, "_update_team_registry", MagicMock()),
            patch("app.core.mounts.get_effective_catalog", AsyncMock(return_value={})),
            patch("app.core.master_rules.load", AsyncMock(return_value="")),
            patch("app.services.claude_token_service.ClaudeTokenService",
                  MagicMock(side_effect=RuntimeError("kein Token im Test"))),
        ]
        return mgr, patches

    async def _neubau(self, weg):
        async with self.Session() as db:
            mgr, patches = self._manager(db, weg)
            for p in patches:
                p.start()
            try:
                await getattr(mgr, weg)("a1")
            finally:
                for p in reversed(patches):
                    p.stop()

    async def test_aktualisieren_beendet_die_haengende_aufgabe(self):
        await self._neubau("update_agent")
        self.assertEqual((await self._aufgabe("alt")).status, TaskStatus.FAILED)
        self.assertEqual((await self._aufgabe("fremd")).status, TaskStatus.RUNNING)
        self.assertEqual((await self._aufgabe("gestoppt")).status, TaskStatus.RUNNING)

    async def test_neustart_beendet_die_haengende_aufgabe(self):
        await self._neubau("restart_agent")
        self.assertEqual((await self._aufgabe("alt")).status, TaskStatus.FAILED)
        self.assertEqual((await self._aufgabe("fremd")).status, TaskStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
