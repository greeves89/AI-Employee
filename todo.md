# AI Employee Platform - TODO

## Completed
- [x] Phase 1: Infrastructure (docker-compose, PostgreSQL, Redis)
- [x] Phase 1: Agent Dockerfile (Python 3.12 + Node.js 22 + Claude Code CLI)
- [x] Phase 1: Agent Core (agent_runner, task_consumer, health, log_publisher)
- [x] Phase 2: Orchestrator DB Models (Agent, Task, TaskLog)
- [x] Phase 2: Orchestrator Core (agent_manager, task_router, load_balancer, file_manager)
- [x] Phase 2: REST API + WebSocket Endpoints
- [x] Phase 3: Frontend (Dashboard, Agents, Tasks, Files, Settings)
- [x] Phase 4: Telegram Bot Integration
- [x] Chat Markdown Rendering + Auto-Scroll
- [x] Dual Auth (API Key + OAuth Token) in Settings
- [x] README.md mit Setup-Anleitung
- [x] Agent Version-based Update Detection + One-Click Update
- [x] Chat Triple-Message Bug Fix (WebSocket Reconnection Cascade)
- [x] Phase 6A: Long-term Memory System (DB + API + Agent CLI + Frontend Tab)
- [x] Phase 6B: Notification System (DB + API + WebSocket Push + Telegram + Frontend Bell)
- [x] Phase 6C: Webhook Endpoint (External event triggers for agents)
- [x] Phase 6D: Approval Workflow (Chat-based via notifications + agent CLI)

- [x] Phase 7A: MCP Server - Memory (native tools for memory_save/search/list/delete)
- [x] Phase 7B: MCP Server - Notifications (notify_user, request_approval)
- [x] Phase 7C: MCP Server - Orchestrator (tasks, team, messages, schedules)
- [x] Phase 7D: MCP Infrastructure (package.json, Dockerfile, .mcp.json auto-config)
- [x] Phase 7E: CLAUDE.md template updated for MCP tools + Web UI explanation
- [x] Phase 8C: Proactive Agent Loop (auto-schedule, queue-check, toggle UI)

## In Progress
- [ ] Phase 5A: Agent Onboarding Interview + Auto-CLAUDE.md
- [ ] Phase 5B: Shared Filesystem + Agent Directory
- [ ] Phase 5C: Files in Chat (Upload/Download/Preview)

## Pending
- [ ] Phase 5D: Agent-to-Agent Messaging
- [ ] Phase 5E: Self-Improvement Loop v2
- [ ] Phase 8A: MCP Service for Gmail/Calendar (OAuth token passthrough)
- [ ] Phase 8B: MCP Service for Google Drive/OneDrive
- [ ] Frontend rebuild + E2E Test
- [ ] Token Refresh Logic (Access Token laeuft nach 8h ab)
- [ ] Security: Workspace Isolation, gefaehrliche Bash-Commands blocken
- [ ] Auto-Scaling: Agents hochfahren bei Queue-Druck
- [ ] Log Persistence: Strukturierte Logs in PostgreSQL
- [ ] Production docker-compose.prod.yml mit nginx
