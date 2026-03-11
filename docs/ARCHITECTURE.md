# Architecture Documentation

This document describes the system architecture, data flows, component interactions, and deployment topology of the AI Employee Platform.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Component Diagram](#component-diagram)
3. [Data Flow](#data-flow)
4. [Component Details](#component-details)
5. [Database Schema Overview](#database-schema-overview)
6. [Network Topology](#network-topology)
7. [Deployment Topology](#deployment-topology)
8. [Technology Stack](#technology-stack)
9. [Key Design Decisions](#key-design-decisions)

---

## System Overview

The AI Employee Platform is a multi-agent orchestration system that allows users to create, manage, and interact with AI agents powered by Claude. Each agent runs as an isolated Docker container and communicates with the user via a real-time WebSocket interface.

**Core capabilities:**
- Spawn isolated AI agent containers on demand
- Real-time bidirectional communication (WebSocket)
- Task queue for async agent work
- Tool use with user-approval workflow for sensitive operations
- Persistent memory and conversation history
- Scheduled (cron-like) agent tasks
- MCP (Model Context Protocol) server integrations
- Audit logging and security controls

---

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CLIENT (Browser)                             │
│                      Next.js Web Application                         │
│   Chat UI │ Agent Dashboard │ Task Monitor │ Settings │ Approvals    │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ HTTPS / WSS
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    TRAEFIK (Reverse Proxy)                            │
│         TLS termination │ Rate limiting │ Security headers            │
└──────────┬──────────────────────────────────────────┬───────────────┘
           │ HTTP                                     │ HTTP
           ▼                                          ▼
┌──────────────────────┐               ┌──────────────────────────────┐
│  FRONTEND SERVICE    │               │    ORCHESTRATOR API           │
│  Next.js / Node.js   │               │    FastAPI (Python)            │
│  Port 3000           │               │    Port 8000                  │
│                      │               │                               │
│  • Static rendering  │               │  • REST API endpoints         │
│  • WebSocket client  │               │  • WebSocket server           │
│  • Real-time updates │               │  • Agent lifecycle mgmt       │
│  • State management  │               │  • Task queue dispatch        │
└──────────────────────┘               │  • Auth / JWT                 │
                                       │  • Approval workflow          │
                                       │  • Audit logging              │
                                       └──────┬───────────────────────┘
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    │                         │                         │
                    ▼                         ▼                         ▼
         ┌──────────────────┐   ┌──────────────────────┐  ┌───────────────────┐
         │   POSTGRESQL     │   │       REDIS           │  │  DOCKER PROXY     │
         │   Port 5432      │   │    Port 6379          │  │  Port 2375        │
         │  (internal only) │   │  (internal only)      │  │  (internal only)  │
         │                  │   │                       │  │                   │
         │  • Users/sessions│   │  • Task queue         │  │  • API allowlist  │
         │  • Conversations │   │  • Pub/sub channels   │  │  • Blocks unsafe  │
         │  • Agent configs │   │  • Approval requests  │  │    operations     │
         │  • Audit logs    │   │  • Session cache      │  │  • Validates      │
         │  • Tasks/todos   │   │  • WebSocket state    │  │    container cfg  │
         │  • Schedules     │   │                       │  │                   │
         └──────────────────┘   └──────────────────────┘  └────────┬──────────┘
                                                                    │
                                                           /var/run/docker.sock (ro)
                                                                    │
                                                                    ▼
                                              ┌─────────────────────────────────┐
                                              │      DOCKER ENGINE (Host)        │
                                              │                                  │
                                              │  ┌──────────┐  ┌──────────┐    │
                                              │  │ Agent-1  │  │ Agent-2  │    │
                                              │  │(container│  │(container│    │
                                              │  │  claude) │  │  claude) │    │
                                              │  └──────────┘  └──────────┘    │
                                              │         agent-network           │
                                              └─────────────────────────────────┘
```

---

## Data Flow

### 1. User Sends a Chat Message

```
Browser
  │── POST /api/chat  OR  WebSocket message
  ▼
Traefik (TLS termination)
  ▼
Orchestrator API
  │── Auth middleware (verify JWT)
  │── Load conversation from PostgreSQL
  │── Look up agent assignment
  ▼
Redis (publish to agent channel)
  ▼
Agent Container (subscribed to channel)
  │── Calls Claude API (Anthropic)
  │── Streams response tokens
  ▼
Redis (publish response stream)
  ▼
Orchestrator WebSocket handler
  ▼
Browser (real-time streaming via WSS)
  │── Tokens appear progressively in UI
  ▼
PostgreSQL (message saved when complete)
```

### 2. Agent Executes a Tool (High-Risk)

```
Agent Container
  │── Evaluates tool call risk level
  │── Risk = "high" → POST /api/approvals/request
  ▼
Orchestrator API
  │── Creates approval record in PostgreSQL
  │── Publishes approval request to Redis
  ▼
WebSocket → Browser
  │── Approval modal shown to user
  │── User clicks Approve / Deny
  ▼
Browser → POST /api/approvals/{id}/decision
  ▼
Orchestrator API
  │── Records decision + audit log
  │── Publishes decision to Redis
  ▼
Agent Container (receives decision)
  │── If approved: executes tool
  │── If denied: returns error to Claude
  ▼
Result streamed back to user
```

### 3. Agent Spawning

```
User → "Create new agent" in UI
  ▼
Orchestrator API (POST /api/agents)
  │── Validates agent configuration
  │── Saves agent record to PostgreSQL
  ▼
Agent Manager (core/agent_manager.py)
  │── Selects template (core/agent_templates.py)
  │── Calls Docker Proxy: POST /containers/create
  ▼
Docker Proxy (allowlist validation)
  │── Validates: no --privileged, no unsafe mounts
  │── Forwards approved request to Docker socket
  ▼
Docker Engine → starts agent container
  ▼
Agent container
  │── Subscribes to Redis channel
  │── Registers with orchestrator
  │── Ready to receive tasks
```

### 4. Scheduled Task Execution

```
Orchestrator scheduler (background task)
  │── Polls PostgreSQL for due schedules
  │── Creates task record
  ▼
Task Router (core/task_router.py)
  │── Routes to appropriate agent
  │── Publishes task to Redis queue
  ▼
Agent Container
  │── Picks up task from queue
  │── Executes, streams results
  ▼
Orchestrator
  │── Updates task status in PostgreSQL
  │── Notifies user via WebSocket / Telegram
```

---

## Component Details

### Frontend (Next.js)

**Location:** `frontend/`

| Module | Purpose |
|--------|---------|
| `src/app/` | Next.js App Router pages |
| `src/components/` | Reusable UI components |
| `src/hooks/` | Custom React hooks (WebSocket, auth, data fetching) |
| `src/store/` | Zustand state management |
| `src/lib/` | API client, utilities |

Key components:
- `components/agents/` — Agent cards, chat interface, approval modal
- `components/tasks/` — Task list, status badges, live progress
- `hooks/useWebSocket.ts` — WebSocket connection with reconnect logic
- `hooks/useAgentStream.ts` — Token streaming for chat responses

### Orchestrator (FastAPI)

**Location:** `orchestrator/`

| Module | Purpose |
|--------|---------|
| `app/main.py` | Application entry point, middleware setup |
| `app/api/` | REST API route handlers |
| `app/core/` | Business logic |
| `app/models/` | SQLAlchemy ORM models |
| `app/schemas/` | Pydantic request/response schemas |
| `app/services/` | External service integrations (Redis, email, etc.) |
| `app/db/` | Database session management, migrations |

Key core modules:
- `core/agent_manager.py` — Spawn, stop, monitor agent containers via Docker Proxy
- `core/task_router.py` — Route tasks to agents, handle load balancing
- `core/auth.py` — JWT generation/validation, OAuth flows
- `core/stream_manager.py` — Manage WebSocket streams and Redis pub/sub

### Agent (Claude Code Runner)

**Location:** `agent/`

Each agent is a Docker container running Claude Code CLI with an MCP server sidecar.

| Module | Purpose |
|--------|---------|
| `app/agent_runner.py` | Main loop: subscribe to Redis, run Claude, stream results |
| `app/chat_handler.py` | Handle chat messages, maintain conversation context |
| `app/task_consumer.py` | Consume async tasks from Redis queue |
| `app/command_filter.py` | Block dangerous shell commands before execution |
| `mcp/` | MCP server implementations |

### Docker Proxy

**Location:** `docker-proxy/`

An `aiohttp`-based HTTP proxy that sits between the orchestrator and the Docker socket.

- `proxy_server.py` — Main proxy implementation
- `allowlist.yml` — YAML allowlist of permitted API operations

Validates:
- Only permitted API endpoints are forwarded (`/containers/create`, `/containers/{id}/start`, etc.)
- No dangerous flags in container create requests (`Privileged`, `PidMode`, `NetworkMode=host`)
- No sensitive host path mounts

### MCP Servers

**Location:** `agent/mcp/`

MCP (Model Context Protocol) servers provide tools to agents:

| Server | Tools Provided |
|--------|---------------|
| `orchestrator` | List agents, create tasks, read todos, send notifications |
| `memory` | Save/search/delete persistent memories |
| `notifications` | Push notifications to user (UI + Telegram) |

---

## Database Schema Overview

**Key tables:**

```
users                    — User accounts, roles, OAuth tokens
sessions                 — Active JWT sessions
api_keys                 — API keys (hashed), permissions
agents                   — Agent configurations and state
conversations            — Chat conversation metadata
messages                 — Individual chat messages (user + agent)
tasks                    — Async task records with status
todos                    — User/agent TODO items
schedules                — Recurring task schedules
audit_logs               — Security audit trail
mcp_servers              — Configured MCP server endpoints
agent_templates          — Reusable agent configuration templates
```

**Storage engines:**
- **PostgreSQL 16** — Primary datastore for all persistent data
- **Redis 7** — Ephemeral: task queues, pub/sub channels, session cache, approval state

---

## Network Topology

```
Host Machine
│
├── Docker Network: internal
│   ├── postgres (5432) — NOT exposed externally
│   ├── redis (6379) — NOT exposed externally
│   ├── orchestrator (8000) — NOT exposed externally (via Traefik)
│   ├── frontend (3000) — NOT exposed externally (via Traefik)
│   └── docker-proxy (2375) — NOT exposed externally
│
├── Docker Network: agent-network
│   ├── orchestrator — bridge between internal and agent-network
│   ├── docker-proxy — bridge between internal and agent-network
│   └── agent-* (dynamic) — each spawned agent container
│
└── Host Network (Traefik only)
    ├── :80  → HTTP → HTTPS redirect
    └── :443 → HTTPS → routes to frontend or orchestrator
```

---

## Deployment Topology

### Single-Node (Default)

All services on one host. Suitable for most deployments.

```
┌──────────────────────────────────────┐
│           Single Host                │
│                                      │
│  Traefik + Frontend + Orchestrator   │
│  PostgreSQL + Redis + Docker Proxy   │
│  Agent containers (dynamic)          │
│  Prometheus + Grafana + Loki         │
└──────────────────────────────────────┘
```

Start: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`

### With Monitoring Stack

```
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  -f docker-compose.monitoring.yml \
  up -d
```

Adds: Prometheus, Grafana, Loki, Node Exporter, cAdvisor.

### Docker Swarm (Production Secrets)

For production secret management:
```
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  -f docker-compose.secrets.yml \
  up -d
```

---

## Technology Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| AI Model | Claude (Anthropic API) | claude-opus-4-6 |
| Agent Runtime | Claude Code CLI | latest |
| Backend API | FastAPI | 0.115+ |
| Backend Language | Python | 3.12 |
| Frontend | Next.js | 15+ |
| Frontend Language | TypeScript | 5+ |
| Database | PostgreSQL | 16 |
| Cache / Queue | Redis | 7 |
| Reverse Proxy | Traefik | v3.2 |
| Container Runtime | Docker | 24+ |
| Monitoring | Prometheus + Grafana + Loki | latest |
| ORM | SQLAlchemy | 2.0 (async) |
| Migrations | Alembic | latest |
| State Management | Zustand | latest |

---

## Key Design Decisions

### 1. One Container Per Agent
Each agent runs in its own Docker container for isolation. This prevents agents from interfering with each other and allows resource limits to be enforced per agent. The tradeoff is higher overhead compared to a single multi-agent process.

### 2. Docker Socket Proxy (Not Direct Mount)
Instead of mounting `/var/run/docker.sock` directly into the orchestrator, all Docker API calls go through a filtering proxy. This prevents the orchestrator from spawning privileged containers even if it were compromised.

### 3. Redis for Real-Time Communication
Redis pub/sub is used for communication between the orchestrator WebSocket handler and agent containers. This allows horizontal scaling (multiple orchestrator instances) and decouples agent execution from HTTP request handling.

### 4. Approval Workflow via WebSocket
High-risk tool approvals use WebSocket (not polling) to ensure low latency and immediate feedback. The approval state is stored in Redis with a TTL so pending approvals expire automatically.

### 5. Async-First Backend
FastAPI with async SQLAlchemy 2.0 ensures the orchestrator handles many concurrent WebSocket connections efficiently without blocking on I/O.

### 6. MCP for Agent Tools
The Model Context Protocol provides a standardized interface for agent tools. This makes it easy to add new capabilities (new MCP servers) without modifying agent code.
