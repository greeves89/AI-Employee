# Changelog

All notable changes to AI-Employee are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) · Versioning: [SemVer](https://semver.org/)

---

## [1.29.0] — 2026-04-24

### Added
- **Agent Detail Modal in Analytics** — Click any agent row in the Analytics dashboard to open a modal with full stats: task volume, success rate, cost, avg turns, daily bar chart (completed vs. failed), recent error log, and latest ratings with comments.
- **`skill_record_usage` MCP tool** — Agents can now explicitly signal "I used skill X during this task" via a new MCP tool. Records a `SkillTaskUsage` entry with task linkage for accurate analytics. `skill_rate` now also calls this internally — one call records both the rating and the usage event.
- **`skill_rate` now tracks task context** — `skill_rate` accepts optional `task_id` (pass `CURRENT_TASK_ID` from prompt) and `helpfulness` (1–5). Usage is linked to the specific task for full traceability.
- **Agent Update All button** — New "Update All (N)" button in the Agents page header appears automatically when one or more agents have an available update. Individual update button also added to the per-card hover actions (orange arrow icon).
- **Dynamic version reading** — `AGENT_VERSION` now reads from the `VERSION` file at runtime instead of being hardcoded in `config.py`. The VERSION file is mounted into the orchestrator container via `docker-compose.yml` so the version endpoint always reflects the actual running release.

### Fixed
- **Version banner false-positive** — `AGENT_VERSION` was hardcoded as `"1.27.0"` even after rebuilding with 1.28.0. Now reads from `VERSION` file dynamically, so the update banner correctly disappears after a rebuild.

---

## [1.28.0] — 2026-04-23

### Added
- **Skill Analytics Dashboard** — New `/analytics` page with platform-wide stats: total tasks, total cost, estimated time saved, avg rating, agent count. Daily task-volume area chart. Sortable skill table with ROI column (manual duration vs. actual agent time). Per-agent performance table with success rate, avg cost, avg duration.
- **Skill time-savings tracking** — New `manual_duration_seconds` field per skill (set in the Skills modal). New `skill_task_usages` table records actual agent duration vs. manual baseline per task. Time-saved is calculated automatically and shown in the analytics dashboard.
- **Skill usage API** — `POST /ratings/skill-usage` to record explicit skill–task pairings; `PATCH /skills/marketplace/{id}/manual-duration` to set the manual-effort baseline for ROI calculation.
- **Analytics sidebar link** — Analytics page added to the main navigation.

### Fixed
- **Multi-user data isolation** — Comprehensive security fix: regular users can no longer read, modify, or delete data belonging to other users. All endpoints now enforce ownership:
  - **Tasks** — list and detail endpoints filtered by user-owned agents
  - **Schedules** — list scoped; all mutations (update / delete / trigger / pause / resume) check agent ownership
  - **Knowledge Base** — fully per-user: 1 KB per user, shared across all of that user's agents, invisible to other users. Agent-facing write/search/read endpoints scope to the agent owner's KB automatically
  - **Approval Rules** — list shows only global + own rules; PATCH/DELETE blocked for foreign rules
  - **Agent Memories** — GET `/memory/agents/{id}` verifies agent ownership before returning
  - **Team Directory** — scoped to user-owned agents for non-admins
  - **Audit Log** — fixed 500 crash (`e.details` → `e.meta`)
- **Host-mount injection into CLAUDE.md** — Configured NFS/SMB/local volume mounts are now listed in the agent's CLAUDE.md so Claude knows which paths are available.
- **Alembic multi-head** — Merge migration added to resolve diverged migration heads after parallel feature branches.

---

## [1.27.0] — 2026-04-23

### Added
- **Native MS Graph MCP server** — 25 tools covering Outlook Mail (read, send, reply), Calendar (list/create/update/delete events), Teams (channels + 1:1 chats), Planner tasks, Microsoft To-Do lists, and OneDrive file search/read. Auto-registered when the agent's user has a connected Microsoft account.
- **Per-user Microsoft OAuth** — Each user connects their own Microsoft 365 account via OAuth. Tokens are stored per-user (not shared globally). Admin configures Azure App Registration credentials once in Settings; each user then signs in individually. `oauth_integrations` table now has a nullable `user_id` column with partial unique indexes.
- **Expanded Microsoft OAuth scopes** — Added `Mail.Send`, `Chat.ReadWrite`, `ChannelMessage.Read.All`, `Tasks.ReadWrite`, `Contacts.ReadWrite`, `People.Read` for full M365 coverage.
- **Integrations page: setup guide** — Microsoft 365 cards show a "Per user" badge and an expandable Azure App Registration guide with copy-able redirect URL and the exact list of required Delegated scopes.

### Fixed
- **Bridge heartbeat / staleness detection** (#135) — Added `bridge_last_seen_at` timestamp (updated on every incoming WebSocket message). `bridge_connected` boolean missed NAT/WiFi drops that don't send TCP FIN; `bridge_last_seen_at` > 20s now marks the bridge as offline regardless. Ping/pong task sends `{"type":"ping"}` every 10s so the timestamp stays fresh while the bridge is idle.
- **Separate bridge status endpoint** — New `GET /computer-use/sessions/{id}/status` lets the UI distinguish "no screenshot yet" from "bridge is gone" without triggering a screenshot request.
- **503 now logged** — Screenshot fetch failures were silently swallowed; `console.warn` now logs the HTTP status code for easier debugging.

---

## [1.26.0] — 2026-04-23

### Added
- **Autonomy Levels L1–L4** — Each agent can be assigned an autonomy level that defines what it may do without asking. L1 = read-only, L2 = recommendations + workspace writes, L3 = full shell + packages, L4 = fully autonomous. Set via agent settings or API (`POST /agents/{id}/autonomy-level`).
- **Whitelist-based approval model** — Replaced the old blacklist approach ("ask before X") with a whitelist ("you are allowed to do X; everything else requires approval"). Safer by default — no gaps where the agent silently acts outside its mandate.
- **DB-backed level presets** — Autonomy preset rules are stored in the `autonomy_preset_rules` table and seeded on startup. Admins can add, edit, and delete rules per level via the UI without touching code.
- **Level-Presets tab in Approvals page** — Third tab shows all four levels with their allowed actions. Inline add/delete per rule. Old blacklist wording auto-detected and migrated to whitelist on first startup.
- **Full governance audit trail** — Every governance-relevant event is now written to `audit_logs`: approval requests, approvals, denials, autonomy level changes, approval rule CRUD, and preset rule changes. Nothing goes untracked.
- **Auto-Preset badge** — Rules generated by autonomy level presets are marked with an "Auto-Preset" badge in the Rules tab so users know which rules are system-managed.
- **Rules tab loads on mount** — Fixed bug where the Rules tab showed 0 entries until clicked; rules now load immediately on page open.

### Changed
- **Prompt injection framing** — `TASK_STARTUP_PREFIX` and `CHAT_STARTUP_PREFIX` updated to whitelist framing. Agents now read their allowed actions first; anything outside the list triggers `request_approval` automatically.
- **New audit event types** — `approval_requested`, `autonomy_level_changed`, `approval_rule_created/updated/deleted`, `preset_rule_added/deleted`, `agent_created/deleted` added to `AuditEventType`.

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
