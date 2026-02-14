# AI Employee Platform

Eine Self-Hosted Plattform, die Claude Code CLI als autonomen AI-Agenten in Docker-Containern betreibt. Steuerbar per Web-UI und Telegram Bot.

## Features

- **Multi-Agent**: Mehrere Claude-Agenten parallel in isolierten Docker-Containern
- **Web UI**: Dashboard mit Live-Chat, Task-Verwaltung, Log-Streaming
- **Telegram Bot**: Aufgaben direkt per Telegram an Agenten senden
- **Load Balancing**: Automatische Verteilung von Tasks auf verfuegbare Agenten
- **File Management**: Workspace-Dateien direkt im Browser anzeigen/bearbeiten
- **Knowledge Base**: Persistentes CLAUDE.md pro Agent

## Tech Stack

| Komponente | Technologie |
|------------|-------------|
| Backend | Python 3.12 + FastAPI + SQLAlchemy (async) |
| Frontend | Next.js 14 + TypeScript + Tailwind CSS |
| Agent Runtime | Claude Code CLI (headless) |
| Datenbank | PostgreSQL |
| Queue/PubSub | Redis |
| Container | Docker + Docker SDK for Python |
| Telegram | python-telegram-bot v21 |

## Quick Start

### Voraussetzungen

- **Docker Desktop** installiert und gestartet
- **Claude Code CLI** installiert (`npm install -g @anthropic-ai/claude-code`)
- **Claude Pro/Team Abo** (fuer den OAuth Token)

### 1. Repository klonen

```bash
git clone https://github.com/greeves89/AI-Employee.git
cd AI-Employee
```

### 2. Claude einloggen (falls noch nicht geschehen)

```bash
claude login
```

Dies speichert einen OAuth Token in deiner macOS Keychain.

### 3. OAuth Token extrahieren

```bash
# Option A: Keychain Access App oeffnen > "claude" suchen > Passwort anzeigen
# Option B: Helper-Script nutzen
./scripts/extract-token.sh
```

Der Token sieht so aus: `sk-ant-oat01-...`

### 4. Environment konfigurieren

```bash
cp .env.example .env
```

Dann `.env` bearbeiten und mindestens diese Werte setzen:

```env
# PFLICHT - dein Claude OAuth Token
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-DEIN-TOKEN-HIER

# PFLICHT - Encryption Key generieren
# Python:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=dein-generierter-key

# OPTIONAL - Telegram Bot
TELEGRAM_BOT_TOKEN=      # von @BotFather
TELEGRAM_CHAT_ID=        # deine Chat-ID
```

### 5. Setup ausfuehren

```bash
chmod +x scripts/setup.sh
./scripts/setup.sh
```

### 6. Starten

```bash
docker compose up --build
```

### 7. Oeffnen

- **Web UI**: http://localhost:3000
- **API Docs**: http://localhost:8000/docs

## Benutzung

### Agent erstellen

1. Web UI oeffnen (http://localhost:3000)
2. "Agents" > "Create Agent"
3. Name vergeben, Model waehlen (Standard: claude-sonnet)
4. Agent starten

### Chat mit Agent

1. Agent-Seite oeffnen
2. Tab "Chat" waehlen
3. Nachricht eingeben und Enter druecken

### Task senden

1. "Tasks" > "New Task"
2. Agent auswaehlen
3. Prompt eingeben
4. Task wird in die Queue gestellt und vom naechsten freien Agent bearbeitet

## Architektur

```
                    +------------------+
                    |   Web UI (:3000) |
                    |   Next.js 14     |
                    +--------+---------+
                             |
                             | REST + WebSocket
                             v
                    +------------------+
                    | Orchestrator     |
                    | FastAPI (:8000)  |
                    +---+----+----+---+
                        |    |    |
              +---------+    |    +---------+
              v              v              v
        +-----------+  +-----------+  +-----------+
        | Agent #1  |  | Agent #2  |  | Agent #N  |
        | Docker    |  | Docker    |  | Docker    |
        | Claude CLI|  | Claude CLI|  | Claude CLI|
        +-----------+  +-----------+  +-----------+
              |              |              |
              v              v              v
        +----------------------------------------+
        |          Redis (PubSub + Queue)        |
        +----------------------------------------+
        |          PostgreSQL (Daten)             |
        +----------------------------------------+
```

## Entwicklung

### Einzelne Services starten

```bash
# Nur Infrastruktur (DB + Redis)
docker compose up -d postgres redis

# Agent-Image bauen
docker build -t ai-employee-agent:latest ./agent

# Frontend (lokal, ohne Docker)
cd frontend && npm install && npm run dev

# Orchestrator (lokal, ohne Docker)
cd orchestrator && pip install -r requirements.txt && uvicorn app.main:app --reload
```

### DB Migrations

```bash
cd orchestrator
alembic revision --autogenerate -m "beschreibung"
alembic upgrade head
```

## Projektstruktur

```
AI-Employee/
├── agent/                  # Docker Container Image
│   ├── app/
│   │   ├── agent_runner.py    # Claude CLI Wrapper
│   │   ├── chat_handler.py    # Interaktiver Chat
│   │   ├── task_consumer.py   # Redis Queue Consumer
│   │   └── config.py
│   ├── Dockerfile
│   └── requirements.txt
├── orchestrator/           # FastAPI Backend
│   ├── app/
│   │   ├── api/               # REST + WebSocket Endpoints
│   │   ├── core/              # Agent Manager, Load Balancer
│   │   ├── models/            # SQLAlchemy Models
│   │   └── schemas/           # Pydantic Schemas
│   ├── alembic/
│   └── requirements.txt
├── frontend/               # Next.js Web UI
│   └── src/
│       ├── app/               # Pages (Dashboard, Agents, Tasks, ...)
│       ├── components/        # React Components
│       ├── hooks/             # Custom Hooks
│       ├── lib/               # API Client, Types, Utils
│       └── store/             # Zustand Stores
├── scripts/
│   ├── setup.sh               # Automatisches Setup
│   └── extract-token.sh       # Token-Extraktion Helper
├── docker-compose.yml
├── .env.example
└── CLAUDE.md                  # Agent-Instruktionen
```

## Troubleshooting

### "Agent is not reachable"
- Ist Docker Desktop gestartet?
- `docker ps` - laeuft der Agent-Container?
- Logs pruefen: `docker logs ai-employee-agent-<id>`

### "Could not connect to agent"
- Ist der Orchestrator gestartet? `docker compose ps`
- WebSocket-URL pruefen (Standard: `ws://localhost:8000`)

### Agents funktionieren nicht
- Ist `CLAUDE_CODE_OAUTH_TOKEN` in `.env` gesetzt?
- Token noch gueltig? Claude Tokens laufen nach einiger Zeit ab
- Neu einloggen: `claude login` und Token aktualisieren

## Lizenz

Privates Projekt.
