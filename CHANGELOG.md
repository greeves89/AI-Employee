# Changelog

All notable changes to AI-Employee are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) · Versioning: [SemVer](https://semver.org/)

---

## [1.25.0] — 2026-04-22

### Fixed
- **WebSocket authentication** — Ticket fetch used `window.location.origin` (port 3000) instead of `getApiUrl()` (port 8000), breaking WebSocket auth on local dev setup. Fixed in `chat.tsx`, `notification-bell.tsx`, `use-websocket.ts`, `tasks/[id]/page.tsx`.
- **Agent create 500 error** — `agent_workspace_size_gb` attribute was missing from `Settings` config, causing a 500 error when creating agents.
- **Setup robustness** — `setup.sh` now generates `API_SECRET_KEY` even when the line is completely missing from `.env`, preventing orchestrator startup failure on existing installs.
- **Caddyfile restored** — Accidentally removed during disk cleanup; restored from git history.

---

## [1.24.0] — 2026-04-22

### Added
- **Per-Agent Idle Timeout & Disk Quota** — Each agent can now configure its own idle timeout and disk quota in Settings. Files tab shows a live disk usage bar based on the agent's individual quota.
- **GitHub Star Button** — Sidebar now shows a "Star on GitHub" button with the live star count from the repository.

### Fixed
- **Disk bar uses per-agent quota** — Disk usage bar in Files tab now correctly reads the agent's own quota instead of the global default.
- **Telegram wake-up** — Always verifies actual Docker container state before skipping wake-up to avoid stale status.
- **Cloudflared tunnel stability** — Added healthcheck and autoheal label to prevent silent tunnel degradation.
- **Skill duplicate names** — Skills can no longer be created with date-suffixed duplicate names.
- **Setup: agent image not found** — `setup.sh` now automatically builds `ai-employee-agent:latest` before starting the stack, preventing "pull access denied" errors on fresh installs.
- **Docker Compose v2 requirement documented** — README and setup.sh now clearly state that Docker Compose v2 (`docker compose`) is required.

---

## [1.23.0] — 2026-04-21

### Added
- **Per-Agent Webhook** — Each agent can individually enable external HTTP access via Settings → Externer Zugriff. Generates a Bearer token on first enable; toggle persists across page reloads. Endpoint: `POST /webhooks/agents/{id}`.
- **MCP Endpoint per Agent** — Every webhook-enabled agent exposes a proper MCP 2025-06-18 Streamable HTTP server at `POST /mcp/agents/{id}`. Compatible with n8n MCP Client Node, Cursor, and other MCP clients. Four tools: `send_task`, `get_task_status`, `get_agent_status`, `list_recent_tasks`.
- **Skill File Attachments** — Skills can now carry file attachments (`.py`, `.js`, `.sh`, `.yaml`, `.json`, `.md`, …, max 10 MB each). Files are stored on a shared volume and automatically pushed to `/workspace/skills/{name}/` inside the agent container when the skill is installed.
- **Sidebar Redesign** — Navigation grouped into four sections (Übersicht, Zusammenarbeit, Automation, System) with collapsible groups. New icon-only collapse mode via a toggle button on the sidebar edge; state persists in localStorage.

### Fixed
- **Task result saved to DB** — Agent text output (`assistant` events) is now collected during execution and written to `tasks.result`. Previously the field was always empty because Claude Code CLI's `result` event is often blank.
- **Webhook toggle state lost on refresh** — `webhook_enabled` and `webhook_token` were missing from `AgentResponse` schema and `get_agent_metrics()`. Toggle now correctly loads saved state on page load.
- **MCP `list_recent_tasks` crash in n8n** — `limit` parameter changed from `"type": "integer"` → `"type": "string"` to match n8n's input handling; backend casts to int safely.
- **MCP `send_task` task not findable** — `send_task` now creates a `Task` DB record (status `QUEUED`) before pushing to Redis, so `get_task_status` can always find the task.

---

## [1.22.0] — 2026-04-20

### Added
- **Trend-Driven Skill Auto-Discovery** — `TrendService` scans GitHub Search API (4 queries) and Hacker News daily for trending AI/agent/MCP repos. New repos are saved as `DRAFT` skills for user review. Security: prompt-injection pattern detection, min. 100 stars threshold, HTML/markdown sanitization before storing any external content.
- **Skill Pending Tab** — New "✨ Ausstehend" tab in the Skills page lists all auto-generated draft skills. Users can approve (→ ACTIVE) or reject (→ ARCHIVED) each one individually.
- **Approve/Reject API** — `POST /marketplace/{id}/approve` and `POST /marketplace/{id}/reject` endpoints for skill moderation.
- **Meeting Room: Parallel Moderator Opening** — Moderator now fires its opening statement as a non-blocking `asyncio.create_task()`, so agents can start immediately without waiting.
- **Meeting Room: Agenda Tracking** — Every moderator prompt now includes a `✓/▶/○` agenda status block so the moderator always knows which phase is active and which are done.
- **Meeting Room: Agent Identity** — Agents prepend a `knowledge.md` read instruction to all meeting turns so they speak as themselves with their own context and skills.
- **Meeting Room: Summary Modal** — Completed meeting cards now have a "Zusammenfassung" button that lazy-loads the full room data and renders the summary with PDF export.

### Fixed
- **Category filter labels** — All categories were showing as "Tools" because `CATEGORY_CONFIG` keys were lowercase while the DB stores uppercase enums (`TOOL`, `WORKFLOW`, etc.). Now correctly shows Templates, Workflows, Patterns, Routinen, Rezepte.
- **Health status "Degraded"** — Dashboard was hitting the Next.js frontend instead of the orchestrator health endpoint. Fixed by adding `/api/v1/health` route alias.
- **Markdown rendering** — `---` now renders as `<hr>`, `>` blockquotes are styled, table borders visible.
- **Skill pending tab type error** — `pendingSkills` was typed as `AgentSkill[]` instead of `MarketplaceSkill[]`, causing build failures.
- **Duplicate `source_repo` field** — Removed duplicate field in `MarketplaceSkill` TypeScript interface.

### Changed
- **Repo links in pending skills** — `source_repo` is now a clickable GitHub link (opens in new tab) in the pending skills tab.
- **PDF export button** — Now visible as a blue labelled button instead of an icon-only low-contrast control.

---

## [1.21.0] — 2026-04-18

### Added
- **Cron scheduling** — Schedules now accept a `cron_expression` (e.g. `0 9 * * 1` = every Monday 9 am) in addition to the existing interval-based mode. 7 presets in the UI (Every day at 9am, Every weekday 8am, …) plus a free-text input. Powered by `croniter`.
- **Audit Log dashboard** — New `/audit` page: summary cards (total/success/blocked/failed events), agent budget progress bars, event-type breakdown with clickable filters, paginated log table with agent/outcome/event-type filters.
- **`claude_md` per template** — Agent templates can now carry a `CLAUDE.md` snippet that is written to `/workspace/CLAUDE.md` when an agent is spawned from that template.
- **GitHub Security Workflow** — Weekly + PR scanning: pip-audit, npm audit, Trivy container scan (SARIF → GitHub Security tab), CodeQL (Python + JS), TruffleHog secret detection.
- **System Status Bar** — Traffic-light style health indicator on the dashboard (API, DB, Redis, Docker + agent count).

### Fixed
- **Skill marketplace 401** — FastAPI route ordering bug: `/agent/available` and `/agent/search` were being matched as `/{agent_id}`, hitting the wrong auth middleware. Routes reordered.
- **Network View conversation modal** — Time filter extended to 7d / 30d (previously maxed at 24h, all messages were older). Silent `catch {}` replaced with visible error display.
- **Task listener** — Startup failures now surface in logs instead of dying silently.

### Changed
- **Claude Code CLI** updated from 2.1.78 → 2.1.114 in agent containers.
- **Agent-to-agent rate limit** — Max 20 messages/min per (from, to) pair via Redis INCR + 60s TTL → HTTP 429.
- **`/team/messages` backend** — Fetch limit scales with time window (100 for <6h, 500 for <24h, 2000 for 7d+).

### Internal
- Alembic migrations: `u5o6p7q8r9s0` (agent_templates.claude_md), `v6p7q8r9s0t1` (schedules.cron_expression)
- `croniter>=2.0` added to orchestrator dependencies

---

## [1.20.0] — 2026-04-16

### Added
- **Skill Marketplace** — Skills as persistent DB entities; per-agent skill assignments; catalog browse with category filter; install/uninstall UI.
- **Per-agent webhook triggers** — Agents fire tasks on incoming webhooks matching source + event type + payload conditions; `{{payload.field}}` interpolation in prompts.
- **Knowledge Feeds** — Scheduled ingestion of external RSS/web sources into the agent knowledge base.
- **Memory system upgrade** — Rooms, supersede chains, multi-strategy scoring (cosine + recency + access_count + tag boost), Redis-cached compressor.

---

## [1.19.0] — 2026-04-04

### Added
- **Meeting Rooms** — Multi-agent round-robin collaboration; DB model, API (CRUD + Start/Stop), Redis queue engine.
- **25 Agent Templates** — Pre-configured roles with icons, categories, recommended skills, default approval rules.
- **OAuth Provider Config UI** — Google/Microsoft/Apple client IDs configurable in Settings page with encrypted storage.
- **Skills Page** — `/skills` catalog with browse, agent picker, install, category filter.

### Fixed
- `/chat` page: `initialSessionId` prop, `createNewSession` reset, agent-switch via key remount.

---

## [1.18.0] — 2026-03-21

### Added
- **Self-improvement loop** — Agents reflect after every task; `ImprovementEngine` distils patterns from ratings.
- **Task ratings** — Telegram inline keyboards for rating completed tasks (1–5 stars).
- **Prometheus metrics** — All services export metrics; Grafana dashboards included.
- **Multi-tenant RLS** — PostgreSQL Row-Level Security on 9 user-scoped tables.

---

*Older history available via `git log --oneline`.*
