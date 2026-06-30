# Teams Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use mindCoder:subagent-driven-development (recommended) or mindCoder:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class, persistent agent Teams (members + a lead) to the AI-Employee orchestrator: build a team in the Agents tab and delegate a task to it; the lead routes subtasks to the right members and consolidates results.

**Architecture:** A new `teams` table + `/api/v1/teams` router. Delegation reuses the existing machinery: `POST /teams/{id}/tasks` creates a task for the team **lead** via `TaskRouter.create_and_route_task`, prepending the team roster + a lead instruction to the prompt (same idea as `_build_approval_rules_prefix`). The lead routes via the existing `create_task` MCP tool; member completions wake the lead via the existing delegation callback (`_notify_delegating_agent`/`wake_agent`, merged as #251). A team-scoped `list_my_team` tool and an Agents-tab UI complete it. **No new orchestration loop.**

**Tech Stack:** FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic, pytest (orchestrator), Next.js/React + TypeScript (frontend).

---

## File Structure

**Backend (`orchestrator/`):**
- Create `app/models/team.py` — `Team` ORM model (mirrors `app/models/meeting_room.py`).
- Create `app/api/teams.py` — `/teams` router (CRUD + members/lead + delegate), mirrors `app/api/meeting_rooms.py`.
- Modify `app/api/router.py` — import + `include_router(teams.router)`.
- Create `app/alembic/versions/c1d2e3f4a5b6_add_teams_table.py` — migration (down_revision = current head `b2c3d4e5f6a7`).
- Modify the orchestrator MCP tool module that defines `list_team`/`create_task` — add `list_my_team` (team-scoped). Find it by grepping the tool registry (Task 5).
- Create `tests/test_teams.py` — API + invariant + delegate tests (tests live at `orchestrator/tests/*.py`; no shared conftest — each test file is self-contained).

**Frontend (`frontend/src/`):**
- Create `components/agents/teams-section.tsx` + `components/agents/create-team-modal.tsx` + `components/agents/delegate-to-team-modal.tsx` — mirror `components/agents/create-agent-modal.tsx`.
- Modify the Agents page (`app/admin/agents/…` or `app/agents/…`) to render the Teams section.
- Add an API client function set for `/teams` (mirror the existing agents API client).

---

## Task 1: `Team` model + migration

**Files:**
- Create: `orchestrator/app/models/team.py`
- Create: `orchestrator/app/alembic/versions/c1d2e3f4a5b6_add_teams_table.py`
- Test: `orchestrator/tests/test_teams.py`

- [ ] **Step 1: Write the model**

```python
# orchestrator/app/models/team.py
"""Team model — a persistent, named group of agents with a designated lead."""

from sqlalchemy import String, Text, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # Agent IDs that belong to this team
    member_agent_ids: Mapped[list] = mapped_column(JSONB, default=list)
    # The lead agent (must be one of member_agent_ids); None until set
    lead_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, default=None)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
```

- [ ] **Step 2: Register the model for metadata**

Confirm `app/models/__init__.py` imports models (grep it). If it lists models, add `from app.models.team import Team  # noqa: F401`. If models are imported elsewhere for Alembic autogenerate, follow that pattern (mirror how `meeting_room` is imported).

Run: `grep -rn "meeting_room" orchestrator/app/models/__init__.py orchestrator/app/alembic/env.py`
Add the `Team` import in the same place(s) `MeetingRoom` appears.

- [ ] **Step 3: Write the migration**

```python
# orchestrator/app/alembic/versions/c1d2e3f4a5b6_add_teams_table.py
"""add teams table

Revision ID: c1d2e3f4a5b6
Revises: b2c3d4e5f6a7
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c1d2e3f4a5b6"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("member_agent_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("lead_agent_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("teams")
```

> Verify the exact `created_at`/`updated_at` column definitions against `app/models/base.py:TimestampMixin` and match them (server_default / onupdate) so autogenerate stays clean.

- [ ] **Step 4: Run the migration against the DB**

Run (in the orchestrator container): `docker exec ai-employee-orchestrator alembic upgrade head`
Expected: `Running upgrade b2c3d4e5f6a7 -> c1d2e3f4a5b6, add teams table` then `Done`.
Verify: `docker exec ai-employee-postgres psql -U ai_employee -d ai_employee -c "\d teams"` shows the table.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/models/team.py orchestrator/app/alembic/versions/c1d2e3f4a5b6_add_teams_table.py orchestrator/app/models/__init__.py
git commit -m "feat(teams): add Team model + migration"
```

---

## Task 2: Team CRUD API + router registration

**Files:**
- Create: `orchestrator/app/api/teams.py`
- Modify: `orchestrator/app/api/router.py`
- Test: `orchestrator/tests/test_teams.py`

- [ ] **Step 1: Write the failing test (create + get)**

```python
# orchestrator/tests/test_teams.py
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app  # adjust import to the actual FastAPI app object
from app.core.auth import create_access_token

# NOTE: mirror auth/db setup from an existing test (e.g. tests/test_oauth_tenant.py).
# This file assumes a helper that yields an admin Bearer header + a clean DB.

@pytest.mark.asyncio
async def test_create_and_get_team(admin_headers):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/v1/teams", headers=admin_headers,
                          json={"name": "Dev-Team", "description": "builds tools"})
        assert r.status_code == 201
        tid = r.json()["id"]
        g = await ac.get(f"/api/v1/teams/{tid}", headers=admin_headers)
        assert g.status_code == 200
        assert g.json()["name"] == "Dev-Team"
        assert g.json()["member_agent_ids"] == []
        assert g.json()["lead_agent_id"] is None
```

> Before writing, READ `tests/test_oauth_tenant.py` (or another `tests/*.py` that calls the API) to copy the exact app-import, DB-setup, and admin-auth fixture pattern this repo uses. Replace `admin_headers` with that pattern.

- [ ] **Step 2: Run it — expect fail (no /teams route)**

Run: `docker exec ai-employee-orchestrator pytest tests/test_teams.py::test_create_and_get_team -v`
Expected: FAIL (404 / route missing).

- [ ] **Step 3: Write the router (CRUD), mirroring `app/api/meeting_rooms.py`**

```python
# orchestrator/app/api/teams.py
"""Teams API — persistent agent teams with a lead, plus task delegation."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth
from app.models.team import Team
from app.models.agent import Agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/teams", tags=["teams"])


class CreateTeam(BaseModel):
    name: str
    description: str = ""
    member_agent_ids: list[str] = []
    lead_agent_id: str | None = None


class UpdateTeam(BaseModel):
    name: str | None = None
    description: str | None = None
    member_agent_ids: list[str] | None = None
    lead_agent_id: str | None = None


def _serialize(t: Team) -> dict:
    return {
        "id": t.id, "name": t.name, "description": t.description,
        "member_agent_ids": t.member_agent_ids or [],
        "lead_agent_id": t.lead_agent_id, "is_active": t.is_active,
        "created_by": t.created_by,
    }


def _validate_lead(member_ids: list[str], lead_id: str | None) -> None:
    if lead_id is not None and lead_id not in (member_ids or []):
        raise HTTPException(status_code=400, detail="lead_agent_id must be one of member_agent_ids")


@router.post("/", status_code=201)
async def create_team(body: CreateTeam, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _validate_lead(body.member_agent_ids, body.lead_agent_id)
    team = Team(
        id=uuid.uuid4().hex[:32], name=body.name, description=body.description,
        member_agent_ids=body.member_agent_ids, lead_agent_id=body.lead_agent_id,
        created_by=getattr(user, "email", None) or str(getattr(user, "id", "")),
    )
    db.add(team)
    await db.commit()
    await db.refresh(team)
    return _serialize(team)


@router.get("/")
async def list_teams(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Team).where(Team.is_active == True))).scalars().all()  # noqa: E712
    return {"teams": [_serialize(t) for t in rows]}


async def _get_team(team_id: str, db: AsyncSession) -> Team:
    t = (await db.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if not t or not t.is_active:
        raise HTTPException(status_code=404, detail="Team not found")
    return t


@router.get("/{team_id}")
async def get_team(team_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    return _serialize(await _get_team(team_id, db))


@router.patch("/{team_id}")
async def update_team(team_id: str, body: UpdateTeam, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    if body.name is not None: t.name = body.name
    if body.description is not None: t.description = body.description
    if body.member_agent_ids is not None: t.member_agent_ids = body.member_agent_ids
    if body.lead_agent_id is not None: t.lead_agent_id = body.lead_agent_id
    _validate_lead(t.member_agent_ids, t.lead_agent_id)
    await db.commit(); await db.refresh(t)
    return _serialize(t)


@router.delete("/{team_id}")
async def delete_team(team_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    t.is_active = False
    await db.commit()
    return {"status": "deleted", "id": team_id}
```

- [ ] **Step 4: Register the router**

Modify `orchestrator/app/api/router.py`: add `teams` to the big `from app.api import …` import line, and add `api_router.include_router(teams.router)` next to `meeting_rooms.router`.

- [ ] **Step 5: Run the test — expect pass**

Run: `docker exec ai-employee-orchestrator pytest tests/test_teams.py::test_create_and_get_team -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/api/teams.py orchestrator/app/api/router.py orchestrator/tests/test_teams.py
git commit -m "feat(teams): CRUD API + router registration"
```

---

## Task 3: Member/lead management + invariant test

**Files:**
- Modify: `orchestrator/app/api/teams.py`
- Test: `orchestrator/tests/test_teams.py`

- [ ] **Step 1: Write the failing tests (members + lead invariant)**

```python
@pytest.mark.asyncio
async def test_lead_must_be_member(admin_headers):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        tid = (await ac.post("/api/v1/teams", headers=admin_headers,
               json={"name": "T", "member_agent_ids": ["a1"]})).json()["id"]
        # setting a lead that is not a member -> 400
        r = await ac.patch(f"/api/v1/teams/{tid}/lead", headers=admin_headers, json={"lead_agent_id": "ghost"})
        assert r.status_code == 400
        # adding the agent then setting it lead -> 200
        await ac.post(f"/api/v1/teams/{tid}/members", headers=admin_headers, json={"add": ["lead1"]})
        ok = await ac.patch(f"/api/v1/teams/{tid}/lead", headers=admin_headers, json={"lead_agent_id": "lead1"})
        assert ok.status_code == 200
        assert ok.json()["lead_agent_id"] == "lead1"
```

- [ ] **Step 2: Run — expect fail (routes missing)**

Run: `docker exec ai-employee-orchestrator pytest tests/test_teams.py::test_lead_must_be_member -v`
Expected: FAIL (404).

- [ ] **Step 3: Add the endpoints to `teams.py`**

```python
class MembersChange(BaseModel):
    add: list[str] = []
    remove: list[str] = []


class SetLead(BaseModel):
    lead_agent_id: str | None


@router.post("/{team_id}/members")
async def change_members(team_id: str, body: MembersChange, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    members = list(t.member_agent_ids or [])
    for a in body.add:
        if a not in members: members.append(a)
    members = [m for m in members if m not in body.remove]
    t.member_agent_ids = members
    if t.lead_agent_id and t.lead_agent_id not in members:
        t.lead_agent_id = None  # lead removed -> clear
    await db.commit(); await db.refresh(t)
    return _serialize(t)


@router.patch("/{team_id}/lead")
async def set_lead(team_id: str, body: SetLead, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    _validate_lead(t.member_agent_ids, body.lead_agent_id)
    t.lead_agent_id = body.lead_agent_id
    await db.commit(); await db.refresh(t)
    return _serialize(t)
```

- [ ] **Step 4: Run — expect pass**

Run: `docker exec ai-employee-orchestrator pytest tests/test_teams.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/api/teams.py orchestrator/tests/test_teams.py
git commit -m "feat(teams): member + lead management with invariant"
```

---

## Task 4: Delegate-to-team (roster injection + reuse `create_and_route_task`)

**Files:**
- Modify: `orchestrator/app/api/teams.py`
- Test: `orchestrator/tests/test_teams.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_delegate_requires_lead(admin_headers):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        tid = (await ac.post("/api/v1/teams", headers=admin_headers, json={"name": "T"})).json()["id"]
        r = await ac.post(f"/api/v1/teams/{tid}/tasks", headers=admin_headers,
                          json={"title": "x", "prompt": "do x"})
        assert r.status_code == 400  # no lead
```

- [ ] **Step 2: Run — expect fail (404, route missing)**

Run: `docker exec ai-employee-orchestrator pytest tests/test_teams.py::test_delegate_requires_lead -v`
Expected: FAIL.

- [ ] **Step 3: Add the roster builder + delegate endpoint**

Read each member's role from its `knowledge.md` first heading. Use the existing knowledge accessor (grep `def get_knowledge` / how `agents/{id}/knowledge` reads it) and mirror it; if the simplest path is the Agent model, read `Agent.name` for the roster and the role line from the knowledge endpoint helper.

```python
async def _team_roster(team: Team, db: AsyncSession) -> str:
    lines = []
    for aid in (team.member_agent_ids or []):
        a = (await db.execute(select(Agent).where(Agent.id == aid))).scalar_one_or_none()
        if not a:
            continue
        role = await _role_summary(aid)  # first non-empty line of knowledge.md, else ""
        tag = " (LEAD)" if aid == team.lead_agent_id else ""
        lines.append(f"- {a.name} [{aid}]{tag}: {role}")
    return "\n".join(lines)


LEAD_INSTRUCTION = (
    "Du bist der LEAD dieses Teams. Zerlege die Aufgabe und delegiere sie via "
    "`create_task` an die passende(n) Rolle(n) im Roster. Warte auf ihre Ergebnisse "
    "(sie kommen als Delegation-Result zurück), konsolidiere und melde das Gesamtergebnis. "
    "Passt keine Rolle, sag das ehrlich — rate nicht.\n\n## Team-Roster\n{roster}\n"
)


class DelegateTask(BaseModel):
    title: str
    prompt: str
    priority: int = 5


@router.post("/{team_id}/tasks", status_code=201)
async def delegate_to_team(team_id: str, body: DelegateTask, request: Request,
                           user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    if not t.lead_agent_id:
        raise HTTPException(status_code=400, detail="assign a lead first")
    if not (t.member_agent_ids or []):
        raise HTTPException(status_code=400, detail="team has no members")
    roster = await _team_roster(t, db)
    prefix = LEAD_INSTRUCTION.format(roster=roster)
    router_ = _get_task_router(request)  # mirror how tasks.py obtains the TaskRouter
    task = await router_.create_and_route_task(
        title=body.title, prompt=prefix + "\n## Aufgabe\n" + body.prompt,
        priority=body.priority, agent_id=t.lead_agent_id,
        metadata={"team_id": t.id, "delegated_to_team": True},
    )
    return {"task_id": task.id, "lead_agent_id": t.lead_agent_id, "status": task.status}
```

> READ `app/api/tasks.py` for: (a) the exact `Request`/`Depends` used to get the `TaskRouter` (`_get_task_router`), and (b) how `task.status` is serialized. Mirror it. Implement `_role_summary(aid)` by mirroring the knowledge read used by `GET /agents/{id}/knowledge`; fall back to `""` on any error (never block delegation on a missing role line).

- [ ] **Step 4: Run — expect pass**

Run: `docker exec ai-employee-orchestrator pytest tests/test_teams.py::test_delegate_requires_lead -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/api/teams.py orchestrator/tests/test_teams.py
git commit -m "feat(teams): delegate-to-team via lead routing (reuses create_and_route_task)"
```

---

## Task 5: `list_my_team` orchestrator MCP tool

**Files:**
- Modify: the orchestrator MCP tool module (find it — see Step 1)

- [ ] **Step 1: Locate the tool registry**

Run: `grep -rln '"list_team"\|name="list_team"\|"create_task"' orchestrator/app | grep -vE 'msgraph|claude_md'`
Open the file that defines `list_team`/`create_task` as MCP tools (the orchestrator MCP server the agents connect to). READ how one tool (e.g. `list_team`) is declared (schema) AND implemented (handler → calls into the orchestrator/DB).

- [ ] **Step 2: Add `list_my_team`**

Mirror `list_team` exactly, but scope to the caller's team: look up the `Team` whose `member_agent_ids` contains the calling agent's id (the handler already has the agent id from the MCP auth context — mirror how `list_team` gets it). Return members + role + state, marking the lead. Reuse `_team_roster`-style data (members from `Team.member_agent_ids`, names/states from `Agent`).

> If an agent is in multiple teams, return all of them grouped by team. Keep the output shape parallel to `list_team`.

- [ ] **Step 3: Restart orchestrator + verify the tool is offered**

Run: `docker restart ai-employee-orchestrator` then check an agent's tool list / the MCP tool catalog includes `list_my_team` (grep orchestrator logs or call the tool-list endpoint the agents use — mirror how `list_team` is verified).

- [ ] **Step 4: Commit**

```bash
git add orchestrator/app/<tool-module>.py
git commit -m "feat(teams): list_my_team MCP tool (team-scoped roster)"
```

---

## Task 6: Frontend — Teams section in the Agents tab

**Files:**
- Create: `frontend/src/components/agents/teams-section.tsx`, `create-team-modal.tsx`, `delegate-to-team-modal.tsx`
- Modify: the Agents page that renders agent management (`frontend/src/app/admin/agents/…` or `frontend/src/app/agents/…`)
- Modify/Create: the API client for `/teams`

- [ ] **Step 1: Read the patterns to mirror**

READ `frontend/src/components/agents/create-agent-modal.tsx` (modal + form + API call + toast patterns) and the existing agents admin page (how lists/cards/tabs are rendered, which UI lib/components, how auth/fetch is done). The Teams UI MUST reuse these components and the existing fetch/auth wrapper — do not introduce new patterns.

- [ ] **Step 2: API client for `/teams`**

Add functions mirroring the existing agents client: `listTeams()`, `createTeam(body)`, `getTeam(id)`, `updateTeam(id, body)`, `deleteTeam(id)`, `changeMembers(id, {add, remove})`, `setLead(id, lead_agent_id)`, `delegateToTeam(id, {title, prompt, priority})`. Use the same base-URL + auth-header helper the agents client uses.

- [ ] **Step 3: `create-team-modal.tsx`**

Mirror `create-agent-modal.tsx`: fields = name, description, multi-select members (from `listAgents()`), lead `<select>` constrained to the selected members. On submit → `createTeam`. Show success/error toast like the existing modals.

- [ ] **Step 4: `teams-section.tsx`**

A list of team cards (name, member avatars via `agent-avatar.tsx`, lead badge), a "New Team" button (opens the create modal), and per-card actions: edit members/lead, and "Delegate task" (opens `delegate-to-team-modal.tsx`).

- [ ] **Step 5: `delegate-to-team-modal.tsx`**

Form: title + prompt (+ optional priority) → `delegateToTeam(teamId, …)`. On success, toast with the returned `task_id` and a link to the Tasks tab.

- [ ] **Step 6: Mount the section**

Render `<TeamsSection/>` in the Agents page (a "Teams" sub-tab or section). Follow the page's existing tab/section structure.

- [ ] **Step 7: Build + smoke test the frontend**

Run: `cd frontend && npm run build` → expect success.
Manual: load the Agents tab, create a team, assign members + lead, delegate a task, confirm the task appears.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/agents/teams-section.tsx frontend/src/components/agents/create-team-modal.tsx frontend/src/components/agents/delegate-to-team-modal.tsx frontend/src/app/<agents-page> frontend/src/<teams-api-client>
git commit -m "feat(teams): Agents-tab UI — build teams, assign members/lead, delegate"
```

---

## Task 7: Integration check (delegate → lead task carries roster) + open PR

**Files:**
- Test: `orchestrator/tests/test_teams.py`

- [ ] **Step 1: Add an integration-ish test (roster reaches the lead task)**

```python
@pytest.mark.asyncio
async def test_delegate_creates_lead_task_with_roster(admin_headers, monkeypatch):
    # Stub create_and_route_task to capture the prompt the lead receives.
    captured = {}
    from app.core import task_router as tr
    async def fake(self, *, title, prompt, priority=1, agent_id=None, metadata=None, **kw):
        captured.update(prompt=prompt, agent_id=agent_id, metadata=metadata)
        class _T: id="t1"; status="queued"
        return _T()
    monkeypatch.setattr(tr.TaskRouter, "create_and_route_task", fake)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        tid = (await ac.post("/api/v1/teams", headers=admin_headers,
               json={"name":"T","member_agent_ids":["lead1","m2"],"lead_agent_id":"lead1"})).json()["id"]
        r = await ac.post(f"/api/v1/teams/{tid}/tasks", headers=admin_headers,
                          json={"title":"build","prompt":"do x"})
    assert r.status_code == 201
    assert captured["agent_id"] == "lead1"
    assert "Team-Roster" in captured["prompt"] and "do x" in captured["prompt"]
    assert captured["metadata"]["team_id"] == tid
```

> Adjust the monkeypatch target/signature to the real `create_and_route_task` location and kwargs.

- [ ] **Step 2: Run the full test file**

Run: `docker exec ai-employee-orchestrator pytest tests/test_teams.py -v`
Expected: all PASS.

- [ ] **Step 3: Commit + push + open PR**

```bash
git add orchestrator/tests/test_teams.py
git commit -m "test(teams): delegate injects roster into the lead task"
git push -u origin feat/teams
gh pr create --repo greeves89/AI-Employee --base main --head feat/teams \
  --title "feat(teams): first-class agent teams with lead-routing" \
  --body "See docs/mindCoder/specs/2026-06-30-teams-feature-design.md. Persistent named teams (members + lead); delegate a task to a team and the lead routes via create_task; results return via the existing delegation callback. MVP scope; Weikert-over-teams deferred to phase 2."
```

---

## Self-Review (against the spec)

- **Data model** → Task 1. ✓
- **CRUD API + member/lead mgmt** → Tasks 2, 3. ✓
- **Delegate-to-team (roster + reuse create_and_route_task + callback)** → Task 4. ✓
- **`list_my_team` tool** → Task 5. ✓
- **Agents-tab UI** → Task 6. ✓
- **Tests (API, invariant, delegate, integration)** → Tasks 2,3,4,7. ✓
- **Error handling** (lead-not-member, no-lead, empty-team) → Tasks 3,4. ✓
- **Non-goals** (MeetingRoom untouched, Weikert phase 2) → respected. ✓

**Known reference points** (the implementer must read-and-mirror, not invent): the test harness (no shared conftest — copy an existing `tests/*.py`), the `TaskRouter` accessor in `app/api/tasks.py`, the `_role_summary` knowledge read, the orchestrator MCP tool module (Task 5 Step 1), and the frontend agents components (Task 6 Step 1). These are explicitly flagged in-task rather than guessed.
