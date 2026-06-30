# Team in Meeting-Room einladen (Convenience pre-fill)

**Status:** Design approved · **Date:** 2026-07-01 · **Target:** greeves89/AI-Employee (extends the Teams feature / PR #256)

## Problem & Goal
A meeting room is created by selecting individual agents (`agent_ids`, 2–6). Now that Teams exist (members + lead), the user wants to invite a **whole team** to a meeting in one click instead of picking each agent. Goal: a team picker in the create-room flow that **pre-fills** the agent selection with the team's members.

## Scope
**In:** A frontend-only "Team auswählen" picker in the meeting-room create flow that, on selection, fills the existing agent multi-select with the team's `member_agent_ids`.

**Out / not needed:**
- **No backend or schema change.** The create-room endpoint and `MeetingRoom` model stay as-is (they take `agent_ids`); the team is resolved to agents purely client-side.
- **Agents carrying meeting TODOs is already implemented** — `meeting_rooms.py:_generate_todo_summary` synthesizes the action-item list and "assigns action items to agents as real tasks" (the v1.85–1.86 work, `AgentTodo` model). No code needed; verify-live optional.
- The room does **not** remember which team it came from (convenience, not a `team_id` link).

## Design (frontend-only)
**File:** `frontend/src/app/meeting-rooms/page.tsx` — the create-room flow where `agent_ids` are chosen today.

1. Add an optional **"Team auswählen"** dropdown above/next to the agent selection, populated via the existing `listTeams()` API client (added with the Teams feature).
2. On selecting a team: set the agent multi-select to the team's `member_agent_ids`. Mark the lead (e.g. a small badge) but it is a normal participant — no special role.
3. **6-member cap** (the meeting endpoint enforces 2–6 agents): if the team has **>6** members, pre-select the first 6 with the **lead first**, and show an inline note: "Team hat mehr als 6 Mitglieder — 6 vorausgewählt, bitte anpassen." If a team has <2 members the user simply adds more (the existing ≥2 validation still applies).
4. After pre-fill the user can still add/remove individual agents — the picker only seeds the selection.

Reuse the existing meeting-room page's components, fetch/auth wrapper, and the agent-selection UI. Do not introduce new patterns or libraries.

## Error Handling
- No teams exist → the picker shows an empty/disabled state ("Keine Teams") and the user selects agents manually as before.
- A team member agent no longer exists → it's simply absent from the selectable agents (filtered out); never block room creation.

## Testing
- No component unit tests exist in this repo → validate via `cd frontend && npm run build` (TypeScript + Next build is the authoritative gate) succeeding with no type errors.
- Manual: open the meeting-room create flow, pick a team, confirm the agent selection fills with its members (lead marked), confirm the >6 cap note, create the room.

## Non-goals
- `team_id` link on rooms, lead-as-moderator automation, syncing later team changes into existing rooms — all out of scope (could be future).
