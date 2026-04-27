# Changelog

All notable changes to AI-Employee are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) · Versioning: [SemVer](https://semver.org/)

---

## [1.32.0] — 2026-04-27

### Added
- **Bridge App — Native macOS UI (AppKit)** — Kompletter Redesign der Tray-App. Alle Dialoge (Einstellungen, Berechtigungen, Status) nutzen jetzt native NSPanel/AppKit statt tkinter. Sauberes macOS-Look-and-Feel mit Retina-Support.
- **Bridge — Ordner-Zugriff konfigurierbar** — Berechtigungen-Dialog hat jetzt eine Ordner-Sektion mit NSOpenPanel-Picker. Konfigurierte Pfade werden in `~/.ai_employee_bridge.json` gespeichert.
- **Bridge — Automatische Session-Wiederherstellung** — `ensure_session()` prüft beim Verbinden ob die gespeicherte Session noch existiert. Bei abgelaufener Session wird automatisch eine neue erstellt. Bei abgelaufenem Token öffnet sich automatisch der Einstellungen-Dialog (via 3s-Timer-Trick für Main-Thread-Safety).
- **Computer-Use `agent_id` Session-Binding** — Sessions können via `PATCH /api/v1/computer-use/sessions/{id}/agent` einem bestimmten Agenten zugewiesen werden. Nur dieser Agent darf dann Commands senden.
- **`computer_use` MCP-Tool für Agenten** — Agenten (Claude Code CLI) haben jetzt `computer_list_sessions`, `computer_screenshot`, `computer_click`, `computer_type`, `computer_key`, `computer_find_element` etc. via `desktop` MCP-Server (`computer-use-server.mjs`).
- **`X-Agent-ID` Header in `computer-use-server.mjs`** — MCP-Server sendet jetzt den `X-Agent-ID` Header bei allen API-Calls. Orchestrator kann damit Agent-HMAC-Token validieren.

### Fixed
- **ObjC Klassen-Namenskonflikt** — Alle drei Dialoge definierten innerhalb ihrer Funktionen eine Klasse `_H(NSObject)`. Zweiter Aufruf crashte mit "ObjC class already registered". Fix: Module-Level Handler-Klassen `_SetupHandler`, `_PermsHandler`, `_StatusHandler` mit State-Dicts.
- **Berechtigungen-Dialog crashte (negative Y-Koordinaten)** — 7 Capability-Rows × 54px passten nicht in H=580. Buttons landeten bei y=−44. Fix: H=700.
- **`NSFont.monospacedSystemFontOfSize_` nicht verfügbar** — Fix: `userFixedPitchFontOfSize_` verwenden.
- **`computer-use` reservierter MCP-Name** — Claude Code CLI lehnte den MCP-Server-Namen `computer-use` als reserviert ab. Umbenannt zu `desktop`.
- **`X-Agent-ID` fehlte in Computer-Use API-Calls** — `computer-use-server.mjs` sendete nur den Bearer-Token, nicht den Agent-ID-Header. Orchestrator lehnte alle Requests mit 401 ab.

---

## [1.31.0] — 2026-04-25

### Added
- **Self-Improvement Engine für Skills** — `ImprovementEngine` erkennt Skills mit avg_helpfulness ≤ 3.0 (min. 5 bewertete Nutzungen) und stellt automatisch einen Verbesserungs-Task in die Agent-Queue. Der Agent analysiert den aktuellen Skill-Inhalt, schreibt ihn neu und ruft `skill_update` auf. Kein direkter Anthropic-API-Key auf dem Orchestrator nötig — nutzt die bestehende OAuth-Infrastruktur.
- **`skill_install` im MCP Skill-Server** — Das Tool fehlte komplett in `skill-server.mjs` (Claude Code Agents). Agents können jetzt auch im Claude-Code-Modus Marketplace-Skills installieren.
- **`user_rating` in `skill_rate`** — Agents können Nutzer-Feedback aus dem Gespräch interpretieren und als `user_rating` (1–5) beim Bewerten übergeben. Analytics zeigt jetzt Agent/User-Rating getrennt.
- **Implicit Usage-Tracking bei `skill_install`** — Wenn ein Agent `skill_install` aufruft während ein Task läuft, wird automatisch ein `SkillTaskUsage`-Record erstellt. Sorgt dafür, dass Installations-Ereignisse in der Nutzungs-Analytics sichtbar sind.

### Fixed
- **Skill Analytics zeigte immer 0 Nutzungen** — Frontend zeigte `period_uses` (nur explizit geratete Usages) statt `usage_count`. Jetzt wird `usage_count` als Haupt-Metrik angezeigt, `period_uses` als optionales Zeit-Sub-Label (z.B. "5 (30d)").
- **Positiver Feedback-Loop bei `usage_count`** — `agent_search_skills` inkrementierte den Top-Skill bei jeder Suche, auch bei leeren Queries. Da die Liste nach `usage_count` sortiert wurde, bekam der meistgenutzte Skill exponentiell mehr Counts. Fix: Implicit Tracking nur noch bei nicht-leerem Suchstring.
- **`skill_update` 403 für zugewiesene Agents** — Endpoint erlaubte Updates nur für den Ersteller. Fix: Agents die einen Skill installiert haben (via `AgentSkillAssignment`) dürfen ihn jetzt ebenfalls aktualisieren — ermöglicht den Self-Improvement-Loop.
- **`skill_rate` erstellte Duplikat-Records** — Bei mehrfachem Aufruf pro Task wurde ein neuer `SkillTaskUsage`-Record erstellt statt upzudaten. Fix: Upsert per `(task_id, skill_id, agent_id)` — `usage_count` wird nur bei neuen Records inkrementiert.
- **Auto-Track-Spam in `_record_skill_usages`** — TaskRouter erstellte bei jedem Task-Abschluss `SkillTaskUsage`-Records für **alle** installierten Skills, unabhängig ob sie genutzt wurden. Fix: Funktion backfilled nur noch Timing-Daten auf bereits existierende Records.
- **`skill_search` Implicit Tracking ohne Task-ID** — Agent-seitige `task_id`-Übergabe war optional und wurde meist weggelassen. Orchestrator löst jetzt server-seitig den laufenden Task des Agents auf.

---

## [1.30.1] — 2026-04-24

### Fixed
- **Webhook-Tasks nicht in Analytics sichtbar** — Webhook-Handler erstellte keinen `Task`-DB-Record beim Queuen. Der TaskRouter fand beim Completion-Event keine Task-ID → Analytics, Kosten-Tracking und `skill_rate` blieben leer. Fix: `Task`-Record wird jetzt synchron beim Queuen angelegt.
- **`skill_search` 500 bei Category-Filter** — PostgreSQL kann `character varying` nicht direkt mit `skillcategory` Enum vergleichen. Fix: `cast(Skill.category, Text) == category.upper()`.
- **`skill_search` "No skills found" bei langen Queries** — `ilike` auf kompletten LLM-Query-String (`"brainstorming ideation workflow for generating app ideas"`) findet nichts. Fix: Query wird in Einzelwörter gesplittet, OR-Verknüpfung über alle Wörter.
- **`skill_install` installiert falsche Skill-ID** — `skill_search`-Antwort enthielt keine sichtbare ID; LLM griff auf halluzinierte ID zurück. Fix: ID prominent in der Antwort mit `skill_install(skill_id=X)` Hinweis.

---

## [1.30.0] — 2026-04-24

### Added
- **`skill_install` Tool** — Agents können Marketplace-Skills jetzt selbst installieren. `skill_search` → `skill_install` → sofortige Nutzung ohne Admin-Eingriff. Neuer Orchestrator-Endpunkt `POST /skills/agent/install/{skill_id}` mit `assigned_by="agent:{id}"`. Skill-Content wird direkt in der Response zurückgegeben.
- **`skill_rate` Tool** — Bisher wurde in `TASK_STARTUP_PREFIX` 4× auf `skill_rate` verwiesen, das Tool existierte aber nicht. Jetzt korrekt implementiert: ruft `POST /skills/agent/record-usage` auf und aktualisiert `avg_rating`, `usage_count` und `time_saved_seconds` in der Datenbank.
- **Skill-Lifecycle vollständig geschlossen** — Vollständiger Loop: User gibt Task → Agent sucht Marketplace (`skill_search`) → Agent installiert passenden Skill (`skill_install`) → führt Task aus → bewertet Skill (`skill_rate`) → User-Feedback fließt über bestehenden Rating-Loop zurück zur Skill-Verbesserung.

### Fixed
- `skill_install` und `skill_rate` zu `ALWAYS_ALLOWED_TOOLS` hinzugefügt — werden nie von Autonomy-Enforcement geblockt.

---

## [1.29.5] — 2026-04-24

### Fixed
- **Custom LLM: Autonomy-Levels L1–L4 durchgesetzt** — Bisher wurden die Whitelist-Regeln nur als Text in den System-Prompt injiziert; GPT-Modelle ignorierten sie bei expliziten User-Anfragen. Fix: Echter Code-Level Enforcement im `ToolExecutor.execute()` — geblockte Tool-Kategorien werden **vor** der Ausführung abgefangen und geben einen `[AUTONOMY BLOCK]`-Fehler zurück, der den Agenten zwingt `request_approval` aufzurufen. Unabhängig vom verwendeten Modell.
- **Custom LLM: Kategorie-Mapping korrigiert** — `bash` war auf `shell` gemappt, DB-Kategorie ist `shell_exec`. L3-Shell-Commands wurden fälschlicherweise geblockt.
- **Custom LLM: L4-Wildcard erkannt** — L4-Preset hat nur `custom`-Kategorie ("Alles erlaubt"). `_get_allowed_categories()` erkennt nun die Wildcard-Regel und gibt `None` zurück (= keine Einschränkung).
- **Custom LLM: Autonomy-Cache-TTL auf 10s reduziert** — Whitelist-Änderungen (Level-Wechsel) propagieren jetzt innerhalb von 10s ohne Agent-Restart.

---

## [1.29.4] — 2026-04-24

### Fixed
- **Custom LLM: Skills nicht injiziert** — `LLMChatHandler` (Chat-Tab) und `LLMRunner` (Webhook/Tasks) riefen `get_skills_context()` nie auf — installierte Skills waren dem Agenten vollständig unbekannt. Fix: Skills werden beim ersten Message in den System-Prompt geschrieben (Chat) bzw. in den System-Prompt der Task-Ausführung (Webhook/Tasks).
- **Custom LLM: Falscher `TOOL_USAGE_RULES`-Import** — `llm_chat_handler.py` importierte `TOOL_USAGE_RULES` aus `runner_hooks`, wo die Konstante nicht existiert. Fix: Import entfernt, Skills direkt ans System-Prompt angehängt.
- **Agent-Template: Hardcodierte Fake-Skills** — `agent_templates.py` hatte `find-skills` und `ui-ux-pro-max` als "Pre-installed Skills" fest eingetragen — unabhängig davon was tatsächlich installiert ist. Fix: Statische Liste entfernt; Agents referenzieren jetzt die dynamisch injizierten Skills am Ende des System-Prompts.

---

## [1.29.3] — 2026-04-24

### Added
- **Skills-Awareness in CLAUDE.md** — Agents wissen jetzt dass Skills als Slash Commands unter `/workspace/.claude/skills/` liegen und prüfen dies automatisch beim Gesprächsstart.
- **Knowledge Base Context beim Gesprächsstart** — DEFAULT_CLAUDE_MD instruiert Agents jetzt gezielt `knowledge_search` für "projects", "preferences" und "architecture" am Anfang jeder Conversation aufzurufen.
- **DB-Skills installierbar** — Marketplace-Skills ohne GitHub-Repo (z.B. vom DevAgent erstellte Skills) können jetzt direkt per base64-Write in den Agent-Container installiert werden.

### Fixed
- **About Modal: Zentrierung** — `framer-motion` überschreibt Tailwind `-translate-x/y-1/2` transforms. Fix: äußeres `div` übernimmt Positionierung, inneres `motion.div` nur noch Animation.
- **About Modal: Nicht klickbar** — `AnimatePresence` kann `motion`-Elemente in Portals nicht tracken → Modal wurde nie gerendert. Fix: `AnimatePresence` entfernt, Portal direkt mit `createPortal` aus statischem Import.
- **About Modal: `require()` in Production** — Dynamisches `require("react-dom")` schlägt in Next.js Production-Build still fehl. Fix: statischer `import { createPortal } from "react-dom"` am Dateianfang.
- **Skill Store: `[object Object]` Fehlermeldung** — FastAPI-422-Validierungsfehler sind Arrays; werden jetzt korrekt per `JSON.stringify` als lesbarer Text angezeigt.
- **Skill Store: DB-Skills ohne `repo` crashten mit 422** — Frontend schickte `undefined` als `repo`-Feld. Fix: `cat.repo || cat.source_repo` als Fallback; für `type: "db"` wird `content` direkt gesendet.
- **CLAUDE.md wird bei Restart nicht aktualisiert** — `restart_agent()` schrieb `/workspace/CLAUDE.md` nie neu (nur `create_agent()` tat das). Fix: Schritt 5b in `restart_agent` schreibt CLAUDE.md mit aktuellem `DEFAULT_CLAUDE_MD` Template neu — Updates propagieren ab sofort bei jedem Restart automatisch.
- **MyAzureAgent: GitHub-Zugriff nach OAuth-Connect** — Token wird nur beim Container-Start injiziert. Agent-Restart nach GitHub-OAuth-Verbindung nötig und dokumentiert.
- **Sidebar Bottom: Sortierung & UserMenu-Position** — UserMenu zurück an letzter Stelle; Reihenfolge: Notifications → Dark Mode → GitHub → Über → Admin → UserMenu.
- **Über Modal: `# Changelog` Heading** — Wird nun per `[&_h1]:hidden` CSS ausgeblendet da Titel bereits im Modal-Header steht.

## [1.29.2] — 2026-04-24

### Added
- **About Modal** — Info-Button (ⓘ) in der Sidebar (collapsed: Icon, expanded: "Über AI Employee" mit Versionsnummer). Klick öffnet Modal mit aktueller Version + vollständigem Changelog direkt aus der API.

### Fixed
- **Custom LLM: SyntaxError in async generator** — `yield from` ist in async-Funktionen nicht erlaubt. Beide Vorkommen in `_stream_chat_with_body` durch `for/yield`-Loop ersetzt. Betraf alle Custom LLM Agents (OpenAI, Azure) — Container crashten beim Start.
- **Version-Banner immer stale** — `AGENT_VERSION`-Env-Var in `docker-compose.yml` wurde nie automatisch aktualisiert. Jetzt wird `./VERSION` als Read-only-Volume nach `/VERSION` gemountet; `_read_version()` liest diesen Pfad zuerst. Version stimmt ab sofort automatisch nach jedem Release.

---

## [1.29.1] — 2026-04-24

### Fixed
- **Agent creation 500 error** — `UnboundLocalError: cannot access local variable 'config'` on agent creation resolved. The variable was referenced before assignment in `agent_manager.py` (leftover from a refactor). New agents correctly start with no mounts.
- **Custom LLM: max_tokens → max_completion_tokens auto-retry** — Newer OpenAI/Azure models (gpt-5.4, o1, o3, etc.) require `max_completion_tokens` instead of `max_tokens`. The provider now detects the mismatch from the 400 error message and retries automatically — no model-name whitelist needed.
- **Chat tab bar layout** — The `+` button and connection status indicator were scrolling out of view when many chat sessions were open. Only the session list now scrolls; the controls stay pinned to the right.
- **Agents: WebSearch enabled by default** — The default CLAUDE.md prompt now explicitly instructs all agents to use `WebSearch` and `WebFetch` for external information (weather, docs, current events). Previously agents would refuse with "I have no internet access" even though the tools were available.

### Added
- **Provider badge for Claude Code agents** — Agent cards now show an orange "Anthropic" badge for `claude_code` agents, making it easy to distinguish them from Custom LLM agents (violet badge with provider name).

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
