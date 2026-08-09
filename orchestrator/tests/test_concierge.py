"""Admin-Concierge (#11) — „laeuft alles?" in einer Antwort.

Die Zahlen gab es alle schon, nur auf fuenf verschiedenen Seiten. Die haeufigsten
Fragen eines Administrators beantwortete keine davon direkt.

Zwei Entscheidungen, die hier festgenagelt werden:
kein Sprachmodell dahinter (ein Concierge, der eine Zahl halluziniert, ist schlimmer
als gar keiner) und eine kurze, serverseitig geprueft feste Aktionsliste.
"""

import unittest
from pathlib import Path

from app.api import concierge

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"


class ActionAllowlistTests(unittest.TestCase):
    def test_nothing_destructive_is_offered(self):
        """Ein Widget in der Ecke ist der falsche Ort, um einen Agenten zu loeschen."""
        for forbidden in ("delete", "remove", "reset", "drop", "wipe", "purge"):
            for action in concierge.SAFE_ACTIONS:
                with self.subTest(action=action, forbidden=forbidden):
                    self.assertNotIn(forbidden, action.lower())

    def test_list_stays_short(self):
        self.assertLessEqual(len(concierge.SAFE_ACTIONS), 6)

    def test_every_action_has_a_german_label(self):
        for action, label in concierge.SAFE_ACTIONS.items():
            with self.subTest(action=action):
                self.assertTrue(label.strip())
                self.assertNotEqual(label, action)

    def test_allowlist_is_enforced_server_side(self):
        """Ein Widget, das nur die sicheren Knoepfe zeigt, ist keine Absicherung —
        jeder kann den Aufruf direkt schicken."""
        src = (ORCH / "app/api/concierge.py").read_text()
        block = src.split("async def concierge_action")[1]
        self.assertIn("SAFE_ACTIONS", block)
        self.assertIn("Aktion nicht erlaubt", block)


class AccessTests(unittest.TestCase):
    SRC = ORCH / "app/api/concierge.py"

    def test_both_endpoints_are_admin_only(self):
        src = self.SRC.read_text()
        self.assertEqual(src.count("Depends(require_admin)"), 2)
        self.assertNotIn("require_auth)", src.replace("require_auth_or_agent", ""))


class NoHallucinationTests(unittest.TestCase):
    SRC = ORCH / "app/api/concierge.py"

    ATTENTION = ORCH / "app/core/attention.py"

    def test_no_language_model_is_involved(self):
        """Bewusst: der Concierge setzt Abfragen zusammen, er formuliert nicht.

        Gilt fuer BEIDE Dateien — die Erkenner sind seit 1.171 ausgelagert, und ein
        Sprachmodell haette sich dort genauso einschleichen koennen.
        """
        for path in (self.SRC, self.ATTENTION):
            src = path.read_text().lower()
            for token in ("anthropic", "openai", "llm", "completion", "prompt"):
                with self.subTest(token=token, file=path.name):
                    self.assertNotIn(token, src)

    def test_stale_threshold_is_shared_with_the_watchdog(self):
        """Sonst steht hier eine andere Zahl als in der Aufgabenliste."""
        src = self.SRC.read_text()
        self.assertIn("_STALE_TASK_THRESHOLD", src)

    def test_verdict_is_derived_from_the_list(self):
        """Die Ampel wird AUS den Punkten gebildet, nicht daneben.

        Vorher stand die Ampel-Bedingung fuer sich und die Liste fuer sich — dann
        koennen beide auseinanderlaufen, und genau das ist passiert: die Liste
        zeigte einen angehaltenen Agenten, die Ampel machte daraus einen Notfall.
        """
        from app.core.attention import BROKEN, WAITING, verdict_for, item

        self.assertEqual(verdict_for([]), "alles ruhig")
        self.assertEqual(
            verdict_for([item("x", WAITING, "t", "d")]), "wartet auf dich"
        )
        self.assertEqual(
            verdict_for([item("x", WAITING, "t", "d"), item("y", BROKEN, "t", "d")]),
            "handlungsbedarf",
        )
        # Und der Endpunkt bildet sie wirklich so — nicht mit einer zweiten Regel.
        self.assertIn("attention.verdict_for(items)", self.SRC.read_text())


class WiringTests(unittest.TestCase):
    def test_router_is_registered(self):
        from app.api.router import api_router

        # FastAPI >=0.141 resolves include_router() lazily: api_router.routes holds
        # _IncludedRouter wrappers (no .path) instead of flat routes. Walk through
        # fastapi.routing.iter_route_contexts() to get the effective paths; fall back
        # to the old flat attribute for pre-0.141 installs.
        try:
            from fastapi.routing import iter_route_contexts

            paths = {rc.path for rc in iter_route_contexts(api_router.routes)}
        except ImportError:
            paths = {r.path for r in api_router.routes if hasattr(r, "path")}
        self.assertIn("/concierge/overview", paths)
        self.assertIn("/concierge/action", paths)

    def test_widget_hides_itself_for_non_admins(self):
        src = (REPO / "frontend/src/components/concierge/concierge-widget.tsx").read_text()
        self.assertIn("isAdmin", src)
        self.assertIn("return null", src)

    def test_actions_ask_before_acting(self):
        src = (REPO / "frontend/src/components/concierge/concierge-widget.tsx").read_text()
        self.assertIn("confirm(", src)

    def test_widget_is_mounted_globally(self):
        src = (REPO / "frontend/src/app/layout.tsx").read_text()
        self.assertIn("ConciergeWidget", src)


if __name__ == "__main__":
    unittest.main()


class VerdictTests(unittest.IsolatedAsyncioTestCase):
    """Die Ampel — gegen echtes SQL, nicht gegen den Quelltext.

    Der Anlass: „angehalten" lag in derselben Liste wie „Fehlerzustand", und die
    Ampel sprang bei beidem auf rot. Ein Nutzer haelt einen Agenten von Hand an, der
    Idle-Stopp haelt ihn an, und beim naechsten Auftrag weckt ``wake_agent`` ihn
    wieder — der Concierge schlug also Alarm ueber das vorgesehene Verhalten der
    Plattform. Eine Ampel, die staendig rot ist, sieht sich nach einer Woche niemand
    mehr an.
    """

    async def asyncSetUp(self):
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.ext.compiler import compiles

        from app.models.agent import Agent
        from app.models.ai_account import AIAccount
        from app.models.command_approval import CommandApproval
        from app.models.oauth_integration import OAuthIntegration
        from app.models.task import Task

        try:
            compiles(JSONB, "sqlite")(lambda *a, **kw: "JSON")
        except Exception:  # noqa: BLE001 — schon registriert
            pass

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, Task, CommandApproval, OAuthIntegration, AIAccount):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _overview(self, agents):
        from types import SimpleNamespace

        from app.models.agent import Agent

        async with self.Session() as db:
            for index, (state, config) in enumerate(agents):
                db.add(Agent(id=f"a{index}", name=f"Agent {index}", state=state,
                             user_id="u1", config=config or {}))
            await db.commit()
            return await concierge.concierge_overview(
                user=SimpleNamespace(id="u1", role="admin", email="a@b.c"), db=db
            )

    async def test_stopped_agents_are_not_an_emergency(self):
        from app.models.agent import AgentState

        out = await self._overview([(AgentState.STOPPED, {}), (AgentState.STOPPED, {})])
        self.assertEqual(out["verdict"], "alles ruhig")
        self.assertEqual(out["agents"]["broken"], [])
        self.assertEqual(len(out["agents"]["resting"]), 2)

    async def test_error_state_is_an_emergency(self):
        from app.models.agent import AgentState

        out = await self._overview([(AgentState.ERROR, {})])
        self.assertEqual(out["verdict"], "handlungsbedarf")
        self.assertEqual(len(out["agents"]["broken"]), 1)

    async def test_stopped_with_responsibilities_waits_for_a_decision(self):
        """Der tut still nichts — das gehoert gesagt, aber nicht als Notfall."""
        from app.models.agent import AgentState

        out = await self._overview([
            (AgentState.STOPPED, {"proactive": {"enabled": True, "responsibilities": ["Buchhaltung"]}}),
        ])
        self.assertEqual(out["verdict"], "wartet auf dich")
        self.assertTrue(out["agents"]["resting"][0]["skips_proactive"])

    async def test_stopped_without_responsibilities_says_it_wakes_up(self):
        from app.models.agent import AgentState

        out = await self._overview([(AgentState.STOPPED, {})])
        self.assertFalse(out["agents"]["resting"][0]["skips_proactive"])

    async def test_running_agents_are_quiet(self):
        from app.models.agent import AgentState

        out = await self._overview([(AgentState.RUNNING, {}), (AgentState.IDLE, {})])
        self.assertEqual(out["verdict"], "alles ruhig")

    async def test_old_field_name_only_carries_real_failures(self):
        """Eine Oberflaeche aus der Zeit davor darf keine ruhenden Agenten mehr als
        Alarm anzeigen."""
        from app.models.agent import AgentState

        out = await self._overview([(AgentState.STOPPED, {}), (AgentState.ERROR, {})])
        self.assertEqual(len(out["agents"]["unhealthy"]), 1)
        self.assertEqual(out["agents"]["unhealthy"][0]["state"], "error")

    def _kinds(self, out):
        return {i["kind"] for i in out["items"]}

    async def test_expired_access_is_surfaced(self):
        """Der wertvollste Punkt: ein abgelaufener Zugang scheitert STILL. Die
        Claude-Agenten hatten das, und das Dazwischenreden im laufenden Turn war
        wochenlang tot, ohne dass irgendwo etwas rot war."""
        from datetime import datetime, timedelta, timezone
        from types import SimpleNamespace

        from app.models.oauth_integration import OAuthIntegration, OAuthProvider

        async with self.Session() as db:
            db.add(OAuthIntegration(
                provider=OAuthProvider.ANTHROPIC,
                access_token_encrypted="x",
                expires_at=datetime.now(timezone.utc) - timedelta(hours=2),
                account_label="bot@example.test",
            ))
            await db.commit()
            out = await concierge.concierge_overview(
                user=SimpleNamespace(id="u1", role="admin", email="a@b.c"), db=db
            )
        self.assertIn("expired_access", self._kinds(out))
        self.assertEqual(out["verdict"], "handlungsbedarf")

    async def test_unhealthy_ai_account_is_surfaced(self):
        from types import SimpleNamespace

        from app.models.ai_account import AIAccount

        async with self.Session() as db:
            # id ist eine Ganzzahl mit Autoincrement — nicht setzen.
            db.add(AIAccount(name="Anthropic Prod", provider_type="anthropic",
                             is_active=True, last_status="auth_failed",
                             last_error="401", models=[], extra={}))
            await db.commit()
            out = await concierge.concierge_overview(
                user=SimpleNamespace(id="u1", role="admin", email="a@b.c"), db=db
            )
        self.assertIn("account_unhealthy", self._kinds(out))

    async def test_exhausted_budget_is_surfaced(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace

        from app.models.agent import Agent, AgentState
        from app.models.task import Task, TaskStatus

        async with self.Session() as db:
            db.add(Agent(id="ab", name="Teuer", state=AgentState.RUNNING, user_id="u1",
                         config={}, budget_usd=10.0))
            db.add(Task(id="t1", title="x", prompt="y", status=TaskStatus.COMPLETED,
                        agent_id="ab", cost_usd=12.0,
                        created_at=datetime.now(timezone.utc)))
            await db.commit()
            out = await concierge.concierge_overview(
                user=SimpleNamespace(id="u1", role="admin", email="a@b.c"), db=db
            )
        self.assertIn("budget", self._kinds(out))

    async def test_quiet_platform_has_an_empty_list(self):
        """Ist nichts zu tun, steht da nichts — und nicht vier Kacheln mit Zahlen."""
        from types import SimpleNamespace

        from app.models.agent import Agent, AgentState

        async with self.Session() as db:
            db.add(Agent(id="a9", name="Ruhig", state=AgentState.RUNNING,
                         user_id="u1", config={}))
            await db.commit()
            out = await concierge.concierge_overview(
                user=SimpleNamespace(id="u1", role="admin", email="a@b.c"), db=db
            )
        self.assertEqual(out["items"], [])
        self.assertEqual(out["verdict"], "alles ruhig")
        # Die Zahlen sind weiterhin da — nur als Fussnote.
        self.assertEqual(out["stats"]["agents"], 1)

    async def test_broken_sorts_before_waiting(self):
        from types import SimpleNamespace

        from app.models.agent import Agent, AgentState
        from app.models.command_approval import ApprovalStatus, CommandApproval

        async with self.Session() as db:
            db.add(Agent(id="ae", name="Kaputt", state=AgentState.ERROR,
                         user_id="u1", config={}))
            db.add(CommandApproval(agent_id="ae", command="user_decision",
                                   description="?", status=ApprovalStatus.PENDING,
                                   meta={"kind": "low_confidence"}))
            await db.commit()
            out = await concierge.concierge_overview(
                user=SimpleNamespace(id="u1", role="admin", email="a@b.c"), db=db
            )
        self.assertEqual(out["items"][0]["severity"], "broken")
