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

## In Progress
- [ ] Phase 5A: Agent Onboarding Interview + Auto-CLAUDE.md
- [ ] Phase 5B: Shared Filesystem + Agent Directory
- [ ] Phase 5C: Files in Chat (Upload/Download/Preview)

## Pending
- [ ] Phase 5D: Agent-to-Agent Messaging
- [ ] Phase 5E: Self-Improvement Loop v2
- [ ] Frontend rebuild + E2E Test
- [ ] Token Refresh Logic (Access Token laeuft nach 8h ab)
- [ ] Security: Workspace Isolation, gefaehrliche Bash-Commands blocken
- [ ] Auto-Scaling: Agents hochfahren bei Queue-Druck
- [ ] Log Persistence: Strukturierte Logs in PostgreSQL
- [ ] Production docker-compose.prod.yml mit nginx
