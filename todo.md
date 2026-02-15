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

## In Progress
- [ ] Phase 5A: Agent Onboarding Interview + Auto-CLAUDE.md
- [ ] Phase 5B: Shared Filesystem + Agent Directory
- [ ] Phase 5C: Files in Chat (Upload/Download/Preview)

## Pending
- [ ] Phase 5D: Agent-to-Agent Messaging
- [ ] Phase 5E: Self-Improvement Loop v2
- [ ] Phase 7A: MCP Service for Gmail/Calendar (OAuth token passthrough)
- [ ] Phase 7B: MCP Service for Google Drive/OneDrive
- [ ] Phase 7C: Proactive Smart Schedules (conditions + agent-initiated tasks)
- [ ] Frontend rebuild + E2E Test
- [ ] Token Refresh Logic (Access Token laeuft nach 8h ab)
- [ ] Security: Workspace Isolation, gefaehrliche Bash-Commands blocken
- [ ] Auto-Scaling: Agents hochfahren bei Queue-Druck
- [ ] Log Persistence: Strukturierte Logs in PostgreSQL
- [ ] Production docker-compose.prod.yml mit nginx
