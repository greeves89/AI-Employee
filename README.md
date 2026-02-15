# AI Employee Platform

Eine Self-Hosted Plattform, die Claude Code CLI als autonomen AI-Agenten in Docker-Containern betreibt. Steuerbar per Web-UI und Telegram Bot.

## Features

- **Multi-Agent**: Mehrere Claude-Agenten parallel in isolierten Docker-Containern
- **Web UI**: Dashboard mit Live-Chat, Task-Verwaltung, Log-Streaming
- **Chat Sessions**: Mehrere Chat-Sitzungen pro Agent mit History
- **Telegram Bot**: Aufgaben direkt per Telegram an Agenten senden
- **Load Balancing**: Automatische Verteilung von Tasks auf verfuegbare Agenten
- **File Management**: Workspace-Dateien direkt im Browser anzeigen/hochladen/herunterladen
- **Knowledge Base**: Persistentes `knowledge.md` pro Agent (Rolle, Skills, Learnings)
- **Agent Updates**: Versionserkennung mit One-Click Update (Daten bleiben erhalten)
- **OAuth Integrations**: Google, Microsoft, Apple Accounts verbinden (verschluesselte Token-Speicherung)
- **Team Collaboration**: Agents koennen untereinander kommunizieren via Shared Volume

## Tech Stack

| Komponente | Technologie |
|------------|-------------|
| Backend | Python 3.12 + FastAPI + SQLAlchemy (async) |
| Frontend | Next.js 14 + TypeScript + Tailwind CSS |
| Agent Runtime | Claude Code CLI (headless, `--output-format stream-json`) |
| Datenbank | PostgreSQL 16 |
| Queue/PubSub | Redis 7 |
| Container | Docker + Docker SDK for Python |
| Telegram | python-telegram-bot v21 |

## Quick Start

### Voraussetzungen

- **Docker Desktop** installiert und gestartet
- **Claude Code CLI** installiert (`npm install -g @anthropic-ai/claude-code`)
- **Authentifizierung** (eine der folgenden Optionen):
  - **Option A**: Claude Pro/Team Abo (OAuth Token - keine Extrakosten)
  - **Option B**: Anthropic API Key (Bezahlung per Token via console.anthropic.com)

### 1. Repository klonen

```bash
git clone https://github.com/MSQKI/ms_ai_employee.git
cd ms_ai_employee
```

### 2. Authentifizierung einrichten

**Option A: Claude OAuth Token (empfohlen bei Claude Pro/Team)**

```bash
# Claude einloggen
claude login

# Token aus macOS Keychain extrahieren
# Keychain Access App > "claude" suchen > Passwort anzeigen
# Oder:
./scripts/extract-token.sh
```

Der Token sieht so aus: `sk-ant-oat01-...`

**Option B: Anthropic API Key**

API Key von https://console.anthropic.com erstellen.

### 3. Environment konfigurieren

```bash
cp .env.example .env
```

Dann `.env` bearbeiten:

```env
# PFLICHT - Authentifizierung (eine Option waehlen)
# Option A: OAuth Token (Claude Pro/Team)
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-DEIN-TOKEN-HIER
# Option B: API Key (Bezahlung per Token)
ANTHROPIC_API_KEY=sk-ant-api-DEIN-KEY-HIER

# PFLICHT - Encryption Key fuer sichere Token-Speicherung generieren
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=dein-generierter-key

# OPTIONAL - Telegram Bot
TELEGRAM_BOT_TOKEN=      # von @BotFather
TELEGRAM_CHAT_ID=        # deine Chat-ID

# OPTIONAL - OAuth Integrations (Google, Microsoft, Apple)
# Siehe Abschnitt "OAuth Integrations" weiter unten
```

### 4. Setup ausfuehren

```bash
chmod +x scripts/setup.sh
./scripts/setup.sh
```

Das Script baut das Agent-Image und richtet die Datenbank ein.

### 5. Starten

```bash
docker compose up --build
```

### 6. Oeffnen

- **Web UI**: http://localhost:3000
- **API Docs**: http://localhost:8000/docs

## Benutzung

### Agent erstellen

1. Web UI oeffnen (http://localhost:3000)
2. "Agents" > "Create Agent"
3. Name vergeben, optional Model und Rolle waehlen
4. Agent startet automatisch und fuehrt ein Onboarding-Interview durch

### Chat mit Agent

1. Agent-Seite oeffnen (auf Agent-Card klicken)
2. Tab "Chat" ist vorausgewaehlt
3. Nachricht eingeben und Enter druecken
4. Neue Chat-Session mit "+" erstellen, zwischen Sessions wechseln

### Tasks & Schedules

- **Einmalige Tasks**: "Tasks" > "New Task" - wird in die Queue gestellt
- **Wiederkehrende Tasks**: "Schedules" > Intervall und Prompt definieren

### Agent Updates

Wenn eine neue Version des Agent-Images verfuegbar ist:
1. Ein "Update"-Badge erscheint auf der Agent-Card
2. Auf der Agent-Detail-Seite wird ein Update-Banner angezeigt
3. Klick auf "Update Now" erstellt den Container mit dem neuen Image neu
4. Alle Daten (Workspace, Chat-History, Knowledge) bleiben erhalten

```bash
# Neues Agent-Image bauen (nach Code-Aenderungen in ./agent/)
docker build -t ai-employee-agent:latest ./agent

# AGENT_VERSION in orchestrator/app/config.py hochsetzen
# Dann Orchestrator neustarten
docker compose restart orchestrator
```

### Knowledge Base

Jeder Agent hat eine `knowledge.md` im Workspace:
- Wird beim Onboarding mit Rolle, Skills und Regeln gefuellt
- Agent liest und aktualisiert sie selbststaendig
- Ueber Tab "Knowledge" im Web UI einsehbar und editierbar

## OAuth Integrations

Verbinde Google, Microsoft oder Apple Accounts, damit Agents auf diese zugreifen koennen.

### Einrichtung

1. **Google**: https://console.cloud.google.com/apis/credentials - OAuth Client erstellen
2. **Microsoft**: https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps - App registrieren
3. **Apple**: https://developer.apple.com/account/resources/identifiers - Service ID erstellen

Redirect-URL fuer alle Provider: `http://localhost:8000/api/v1/integrations/{provider}/callback`

### Konfiguration in `.env`

```env
OAUTH_GOOGLE_CLIENT_ID=deine-client-id
OAUTH_GOOGLE_CLIENT_SECRET=dein-client-secret
OAUTH_MICROSOFT_CLIENT_ID=deine-client-id
OAUTH_MICROSOFT_CLIENT_SECRET=dein-client-secret
```

### Nutzung

1. Web UI > "Integrations" - Account verbinden
2. Agent-Detail > Tab "Integrations" - Pro Agent aktivieren/deaktivieren

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
cd orchestrator && pip install -e . && uvicorn app.main:app --reload
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
├── agent/                     # Docker Container Image
│   ├── app/
│   │   ├── agent_runner.py       # Claude CLI Wrapper
│   │   ├── chat_handler.py       # Interaktiver Chat
│   │   ├── task_consumer.py      # Redis Queue Consumer
│   │   └── config.py
│   ├── scripts/
│   │   └── ai-team               # CLI fuer Inter-Agent Kommunikation
│   └── Dockerfile
├── orchestrator/              # FastAPI Backend
│   ├── app/
│   │   ├── api/                  # REST + WebSocket Endpoints
│   │   ├── core/                 # Agent Manager, Load Balancer, OAuth, Encryption
│   │   ├── models/               # SQLAlchemy Models (Agent, Task, OAuth, Chat)
│   │   ├── schemas/              # Pydantic Schemas
│   │   └── services/             # Docker, Redis, OAuth Services
│   ├── alembic/                  # DB Migrations
│   └── Dockerfile
├── frontend/                  # Next.js Web UI
│   └── src/
│       ├── app/                  # Pages (Dashboard, Agents, Tasks, Integrations, ...)
│       ├── components/           # React Components
│       ├── hooks/                # Custom Hooks (useWebSocket, usePolling)
│       └── lib/                  # API Client, Types, Utils
├── scripts/
│   ├── setup.sh                  # Automatisches Setup
│   └── extract-token.sh          # Token-Extraktion Helper
├── docker-compose.yml
├── .env.example
└── CLAUDE.md
```

## Troubleshooting

### "Agent is not reachable"
- Ist Docker Desktop gestartet?
- `docker ps` - laeuft der Agent-Container?
- Logs pruefen: `docker logs ai-agent-<name>-<id>`

### "Could not connect to agent"
- Ist der Orchestrator gestartet? `docker compose ps`
- WebSocket-URL pruefen (Standard: `ws://localhost:8000`)

### Agents funktionieren nicht
- Ist `CLAUDE_CODE_OAUTH_TOKEN` oder `ANTHROPIC_API_KEY` in `.env` gesetzt?
- Token noch gueltig? Claude Tokens laufen nach einiger Zeit ab
- Neu einloggen: `claude login` und Token aktualisieren
- Agent-Logs: `docker logs ai-agent-<name>-<id> --tail 50`

### OAuth Token abgelaufen
- Platform refresht Tokens automatisch im Hintergrund (alle 5 Minuten)
- Falls Token trotzdem ungueltig: Account unter "Integrations" trennen und neu verbinden

### ENCRYPTION_KEY verloren/geaendert
- **Alle verschluesselten OAuth-Tokens werden ungueltig!**
- Accounts unter "Integrations" trennen und neu verbinden
- ENCRYPTION_KEY **niemals** committen oder teilen

## Lizenz

Privates Projekt.
