# Teams — First-Class Agent Teams with Lead-Routing

**Status:** Design approved (MVP scope) · **Date:** 2026-06-30 · **Target:** greeves89/AI-Employee

## Problem & Goal

Today the platform has exactly **one implicit, flat "team"** — all agents, surfaced via `team.json` / the `list_team` tool. Multi-agent collaboration only exists as **MeetingRooms**: ephemeral, discussion-oriented sessions that produce action items / TODOs / follow-ups.

There is no way to:
- compose a **persistent, named team** of selected agents,
- give that team a **lead** who knows the members' capabilities,
- **delegate a unit of work to the team** and have the lead route it to the right role.

This is exactly the pattern we build by hand today (an "Atlas" orchestrator agent + specialist agents wired through `knowledge.md` prompts). This spec makes it a **first-class feature**: persistent Teams for work delegation, reusing the existing `create_task` + delegation-callback machinery.

**Goal:** In the Agents tab, a user can build a Team, assign agents, designate a lead, and delegate a task to the team. The lead receives the task plus the team roster, routes subtasks to the right member(s) via `create_task`, the members' results flow back to the lead via the delegation callback, and the lead consolidates and reports back.

## Scope

**In (this MVP / first PR):**
- `Team` data model + migration
- Team CRUD API + member/lead management
- **Delegate-task-to-team** with lead-routing (reuses `create_task` + delegation callback)
- Lead context: team-roster injection + a team-scoped `list_my_team` tool
- Agents-tab UI: create team, assign members + lead, list teams, delegate a task

**Out (later phases):**
- **Weikert "above all teams"** — a global bridge that picks the right team for a user request. MVP: the user delegates **directly to a chosen team**. (Phase 2)
- Structured/iterative in-team review loops (the parked "iterative 3-lens review" design can later become a team behavior)
- Cross-team collaboration, team-level scheduling, team analytics, a dedicated per-agent `capabilities` field

## Data Model

New table `teams` (follows the `MeetingRoom` conventions — JSONB for agent lists, short string id, `TimestampMixin`):

| Column | Type | Notes |
|--------|------|-------|
| `id` | `str` (PK, 32) | short id |
| `name` | `str` (200) | |
| `description` | `Text` | default `""` |
| `member_agent_ids` | `JSONB list[str]` | agents in the team |
| `lead_agent_id` | `str \| None` | must be one of `member_agent_ids` |
| `created_by` | `str \| None` | |
| `is_active` | `bool` | default `True` (soft-delete) |
| `created_at` / `updated_at` | via `TimestampMixin` | |

**Invariants:**
- `lead_agent_id`, if set, MUST be in `member_agent_ids` (enforced in the API layer).
- An agent MAY belong to multiple teams.
- When an agent is deleted, it is removed from every team's `member_agent_ids` (and cleared as lead if applicable). MVP: enforce on agent-delete; defensively filter unknown ids on read.

**Migration:** one Alembic revision adding the `teams` table (head currently `b2c3d4e5f6a7`).

## API (`/api/v1/teams`)

Auth follows existing endpoints: `require_auth` (user JWT) for management, `require_auth_or_agent` where agents call (e.g. the lead's `list_my_team`). Team access mirrors agent-access rules (admin/manager: all; member/viewer: own/assigned).

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/teams` | create `{name, description?, member_agent_ids?, lead_agent_id?}` |
| GET | `/teams` | list (scoped by role) |
| GET | `/teams/{id}` | detail, with resolved member info (name, role, state) |
| PATCH | `/teams/{id}` | update name/description/members/lead |
| DELETE | `/teams/{id}` | soft-delete (`is_active=False`) |
| POST | `/teams/{id}/members` | add/remove members `{add?: [...], remove?: [...]}` |
| PATCH | `/teams/{id}/lead` | set lead (must be a member → 400 otherwise) |
| POST | `/teams/{id}/tasks` | **delegate a task to the team** (the core) |

Validation errors return 400 with a clear message (lead-not-member, no-lead-on-delegate, empty-team-on-delegate).

## Delegate-to-Team (orchestration — reuse, not rebuild)

`POST /teams/{id}/tasks {title, prompt, priority?}`:

1. Resolve the team; require an **active lead** (`lead_agent_id`) and ≥1 member, else 400.
2. Build the **team roster context**: for each member, `name` + a short **role summary** (the first non-empty heading of its `knowledge.md`, e.g. `# Rolle: …`), plus which member is the lead.
3. Create a task **for the lead agent** via the existing `TaskRouter` task-creation path, **prepending** to the prompt:
   - the roster, and
   - a short lead instruction: *"You are this team's lead. Route the work to the right member(s) via `create_task`. Wait for their results, consolidate, and report back. If no member fits, say so — don't guess."*
   This mirrors how the **approval-rules prefix** is already injected into task prompts today (`_build_approval_rules_prefix`).
4. The lead executes: reads the roster, routes subtasks via `create_task` to the right member(s). Member completions notify the lead through the **existing delegation callback** (`_notify_delegating_agent` → `wake_agent`, the resume fix already in main as #251). The lead consolidates and reports up.

**No new orchestration loop.** The only new orchestration code is the **roster + lead-instruction injection**; routing/convergence reuse `create_task` + the delegation callback.

**New tool `list_my_team` (orchestrator MCP):** returns the **team-scoped** roster (members + roles + states) for the calling lead, so it can re-query mid-task. (The existing `list_team` returns *all* agents; this is the team-filtered variant.)

## Lead "knows the capabilities"

- **MVP:** role summary = the `# Rolle:` / first heading of each member's `knowledge.md`, injected into the lead's task context and available via `list_my_team`.
- **Future (out of scope):** a dedicated structured `capabilities` field per agent for sharper routing.

## UI — Agents tab → Teams

A **"Teams" section** within the existing Agents view, following the tab's existing components/patterns:
- **Create Team:** name, description, multi-select members (from existing agents), **lead dropdown** constrained to the selected members.
- **Team list:** cards with name, member avatars, a lead badge, active state.
- **Team detail:** edit members/lead; a **"Delegate task"** action (title + prompt → `POST /teams/{id}/tasks`) that surfaces the resulting lead task.

## Error Handling

- `lead_agent_id` not in members → 400.
- delegate with no lead → 400 ("assign a lead first").
- delegate to an empty team → 400.
- a member agent is stopped → handled by the existing wake-on-dispatch (dispatch `wake_agent` + #251 callback wake).
- lead can't find a suitable member → it **reports back honestly** rather than guessing (enforced via the lead instruction).

## Testing

- **API:** create/list/update/delete; member/lead invariants (lead-must-be-member); delegate creates a lead task carrying the roster; auth/role scoping.
- **Orchestration (integration):** delegate-to-team → lead task created with roster → lead routes via `create_task` → member completes → callback wakes lead → lead consolidates. Reuse existing task/delegation test patterns under `orchestrator/tests`.
- **Migration:** `teams` table present after `alembic upgrade head`.

## Non-goals / Risks

- **MeetingRoom is untouched** — Teams (persistent work delegation) and MeetingRooms (ephemeral discussion) are distinct concepts that coexist.
- **Routing quality is prompt-driven** (the lead reasons over the roster, like the manual Atlas pattern today). Acceptable for MVP; structured capability-matching is a later enhancement.
- **Weikert integration is deferred** to Phase 2 (global request → team selection).
