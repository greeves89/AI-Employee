# AI Employee Platform - TODO

## Completed ✅
- [x] Phase 1: Infrastructure (docker-compose, PostgreSQL, Redis)
- [x] Phase 1: Agent Dockerfile (Python 3.12 + Node.js 22 + Claude Code CLI + gh CLI)
- [x] Phase 1: Agent Core (agent_runner, task_consumer, chat_consumer, health, log_publisher)
- [x] Phase 2: Orchestrator DB Models (Agent, Task, TaskLog, ChatMessage, Memory, Notification, etc.)
- [x] Phase 2: Orchestrator Core (agent_manager, task_router, load_balancer, file_manager)
- [x] Phase 2: REST API + WebSocket Endpoints (agents, tasks, chat, files, memory, notifications)
- [x] Phase 3: Frontend (Dashboard, Agents, Tasks, Files, Settings, Integrations, Admin)
- [x] Phase 4: Telegram Bot Integration
- [x] Chat System (Sessions, Markdown Rendering, Auto-Scroll, WebSocket Streaming)
- [x] Dual Auth (API Key + OAuth Token) in Settings
- [x] README.md mit Setup-Anleitung
- [x] Agent Version-based Update Detection + One-Click Update
- [x] Chat Triple-Message Bug Fix (WebSocket Reconnection Cascade)
- [x] Phase 6A: Long-term Memory System (DB + API + MCP Tools + Frontend Tab)
- [x] Phase 6B: Notification System (DB + API + WebSocket Push + Telegram + Frontend Bell)
- [x] Phase 6C: Webhook Endpoint (External event triggers for agents)
- [x] Phase 6D: Approval Workflow (Chat-based via notifications + MCP tools)
- [x] Phase 7A: MCP Server - Memory (memory_save/search/list/delete)
- [x] Phase 7B: MCP Server - Notifications (notify_user, request_approval)
- [x] Phase 7C: MCP Server - Orchestrator (tasks, team, messages, schedules, TODOs)
- [x] Phase 7D: MCP Infrastructure (package.json, Dockerfile, claude mcp add registration)
- [x] Phase 7E: CLAUDE.md template updated for MCP tools + Web UI explanation
- [x] Phase 8C: Proactive Agent Loop (auto-schedule, queue-check, toggle UI)
- [x] User Authentication (JWT + httpOnly cookies, SSO: Google/Microsoft/Apple)
- [x] Agent Ownership & Access Control (multi-user support)
- [x] Permission Packages (configurable sudo rules per agent)
- [x] OAuth Integrations (Google, Microsoft, Apple, GitHub - encrypted token storage)
- [x] TODOs System (Database + API + MCP Tools + Frontend Tab)
- [x] Schedules (Recurring Tasks)
- [x] Agent Templates System
- [x] Feedback System (with GitHub Issue creation)
- [x] File Upload/Download API
- [x] Security: Rate Limiting (120 req/min), Security Headers, CORS
- [x] Database Migrations (Alembic)
- [x] ai-team CLI (helper for agents to call orchestrator API)

## In Progress 🔄
- [ ] Phase 5A: Agent Onboarding Interview + Auto-CLAUDE.md Generation
- [ ] Phase 5B: Shared Filesystem (/shared/ volume between agents)
- [ ] Phase 5C: Files in Chat (Upload/Download/Preview integration in chat UI)

## Pending 📋
### Features
- [ ] Phase 5D: Agent-to-Agent Messaging (P2P communication beyond shared filesystem)
- [ ] Phase 5E: Self-Improvement Loop v2 (enhanced learning from mistakes)
- [ ] Phase 8A: MCP Service for Gmail/Calendar (OAuth token passthrough)
- [ ] Phase 8B: MCP Service for Google Drive/OneDrive
- [ ] Auto-Scaling: Agents hochfahren bei Queue-Druck

### Testing & Quality
- [ ] Unit Tests (orchestrator/tests/, agent/tests/ aktuell leer)
- [ ] Integration Tests
- [ ] E2E Tests (Frontend)
- [ ] Test Coverage > 80%

### Security Enhancements
- [ ] Command Filtering (gefährliche Bash-Commands blocken: rm -rf /, dd, mkfs, etc.)
- [ ] Workspace Isolation härten (chroot, seccomp, AppArmor)
- [ ] Network Policies (restrict agent outbound traffic)
- [ ] Input Validation härten (zusätzliche Pydantic validators)
- [ ] Secrets Scanning (prevent committing tokens)
- [ ] Security Audit (penetration testing)

### Production Readiness
- [ ] OAuth Token Auto-Refresh (Access Token läuft nach 8h ab)
- [ ] Log Persistence (strukturierte Logs in PostgreSQL statt nur Redis)
- [ ] docker-compose.prod.yml finalisieren (nginx reverse proxy, SSL)
- [ ] Traefik Integration (bereits Ordner vorhanden, aber nicht konfiguriert)
- [ ] Backup Strategy (automated DB + workspace backups)
- [ ] Monitoring & Alerting (Prometheus + Grafana)
- [ ] Documentation (API docs, Architecture docs, Deployment guide)

### Enterprise/B2B Features (CRITICAL für SaaS-Vision!)
- [ ] **Multi-Tenancy** (Organizations/Companies mit Tenant-Isolation)
  - [ ] Organization Model (companies/tenants)
  - [ ] Organization Users & Roles (Admin, Manager, Member per Org)
  - [ ] Tenant-scoped Data (alle Models + org_id)
  - [ ] Cross-Tenant Data Isolation (Row-Level Security)
- [ ] **Billing & Subscription Management**
  - [ ] Stripe/Chargebee Integration
  - [ ] Subscription Plans (Free, Pro, Enterprise)
  - [ ] Usage Tracking (Tokens, API calls, Agent runtime)
  - [ ] Budget Limits & Alerts per Organization
  - [ ] Cost Attribution (per Agent, per User)
- [ ] **Enterprise SSO & Provisioning**
  - [ ] SAML 2.0 Support
  - [ ] Okta/Auth0/Azure AD Enterprise Connectors
  - [ ] SCIM (Automated User Provisioning/Deprovisioning)
  - [ ] JIT (Just-in-Time User Creation)
  - [ ] SSO Enforcement (force SSO for organization)
- [ ] **Compliance & Governance**
  - [ ] GDPR Compliance (Data Export, Right to be Forgotten, Consent Management)
  - [ ] HIPAA Readiness (PHI handling, BAA agreements)
  - [ ] SOC2 Type II Certification
  - [ ] Audit Logs (immutable, tamper-proof activity logs)
  - [ ] Data Residency (EU vs US agent deployment)
  - [ ] Data Retention Policies
- [ ] **Agent Marketplace & Sharing**
  - [ ] Agent Visibility Levels (Private, Team, Organization, Public)
  - [ ] Internal Agent Marketplace (discover/install agents from colleagues)
  - [ ] Agent Publishing Workflow (submit → review → approve)
  - [ ] Agent Reviews & Ratings
  - [ ] Agent Versioning & Changelog
  - [ ] Template Sharing (org-wide templates)
- [ ] **Team Collaboration**
  - [ ] Shared Workspaces per Team/Department
  - [ ] Agent Delegation (assign agent to team member)
  - [ ] Approval Workflows (agent actions need manager approval)
  - [ ] Agent Activity Dashboard (who used which agent when)

### Nice-to-Have
- [ ] Advanced Analytics Dashboard (usage metrics, agent performance)
- [ ] Plugin System (custom MCP servers via UI)
- [ ] White-Label Support (custom branding per organization)
- [ ] API Rate Limits per Organization
- [ ] Webhook Management UI
- [ ] Agent Performance Benchmarks
- [ ] A/B Testing for Agent Prompts
