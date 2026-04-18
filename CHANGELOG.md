# Changelog

All notable changes to AI-Employee are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) · Versioning: [SemVer](https://semver.org/)

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
