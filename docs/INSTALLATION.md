# Installation Guide

## System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 4 cores | 8+ cores |
| RAM | 8 GB (trial, 1–2 working agents) | 16 GB (3 agents), 32 GB (10 agents) |
| Disk | 64 GB SSD | 128 GB+ NVMe (≈25 GB base + up to 10 GB per agent) |
| OS | Ubuntu 22.04 / Debian 12 / macOS 13+ | Ubuntu 24.04 LTS |
| Docker | 24.0+ | Latest |
| Docker Compose | 2.20+ | Latest |

Sizing per edition (measured values, 3/10/30/100 agents): see the user manual, chapter 37.0 "Hardware je Edition".

## Prerequisites

### 1. Docker

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker --version
docker compose version
```

### 2. Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
claude --version
```

### 3. Authentication (choose one)

**Option A: OAuth Token (Claude Pro/Team — no extra cost)**

```bash
claude login
# Follow the browser prompt to authenticate
./scripts/extract-token.sh  # Extract token for .env
```

**Option B: Anthropic API Key (pay-per-token)**

Create an API key at https://console.anthropic.com/settings/api-keys

## Installation Steps

### Step 1: Clone the Repository

```bash
git clone https://github.com/greeves89/AI-Employee.git
cd AI-Employee
```

### Step 2: Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```env
# Required: authentication (choose one)
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-YOUR-TOKEN
# OR
ANTHROPIC_API_KEY=sk-ant-api-YOUR-KEY

# Required: security (generate strong values!)
API_SECRET_KEY=$(openssl rand -hex 32)
ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
DB_PASSWORD=$(openssl rand -hex 16)

# Optional: Telegram bot
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id

# Optional: custom domain (for production)
DOMAIN=your-domain.com
ACME_EMAIL=admin@your-domain.com
```

### Step 3: Start the Stack

**Development (local only):**

```bash
docker compose up -d --build
```

**Production (with HTTPS via Traefik):**

```bash
# Ensure DOMAIN and ACME_EMAIL are set in .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

**With Monitoring:**

```bash
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d --build
```

### Step 4: Initialize Database

```bash
docker compose exec orchestrator alembic upgrade head
```

### Step 5: Create First User

Open your browser to `http://localhost:3000` (or `https://your-domain.com`).

The first user to register automatically becomes the administrator.

### Step 6: Create Your First Agent

1. Click **"New Agent"** in the Web UI
2. Choose an agent template or configure manually
3. Click **Start** — the agent container will be created and started

## Firewall Configuration

If exposing to the internet, restrict direct container port access:

```bash
# Allow only Traefik/nginx proxy ports
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Block direct backend access from outside
sudo ufw deny 8000/tcp
sudo ufw deny 3000/tcp
sudo ufw deny 5432/tcp
sudo ufw deny 6379/tcp

sudo ufw enable
```

## Automated Backups

Install daily automated backups:

```bash
# Using systemd (recommended)
sudo ./scripts/install-backup-cron.sh --systemd --backup-dir /var/backups/ai-employee

# Using cron (fallback)
sudo ./scripts/install-backup-cron.sh --backup-dir /var/backups/ai-employee
```

## Monitoring (Optional)

Start the Prometheus + Grafana monitoring stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d
```

Access:
- **Grafana**: http://localhost:3001 (admin/admin on first boot)
- **Prometheus**: http://localhost:9090

## Verification

```bash
# Check all containers are running
docker compose ps

# Check orchestrator health
curl http://localhost:8000/health | python3 -m json.tool

# Expected output:
# {
#   "status": "healthy",
#   "service": "orchestrator",
#   "checks": {
#     "database": {"status": "healthy"},
#     "redis": {"status": "healthy"},
#     "docker": {"status": "healthy", "agent_containers": 0}
#   }
# }
```

## Troubleshooting

**Containers won't start:**
```bash
docker compose logs orchestrator
docker compose logs frontend
```

**Database connection errors:**
```bash
docker compose exec postgres psql -U postgres -c "SELECT 1"
```

**Claude authentication failures:**
```bash
# Verify token is set
docker compose exec orchestrator env | grep -E "CLAUDE|ANTHROPIC"
```

**Agent containers not creating:**
```bash
# Check Docker socket permissions
ls -la /var/run/docker.sock
# Check docker-socket-proxy logs:
docker compose logs docker-socket-proxy
```

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for more detailed debugging steps.

## Updating

```bash
./scripts/update.sh
```

`update.sh` takes a backup first (and aborts if that fails), pulls the repository, fetches
the ready-made images of that version, verifies their signature before using them (building
locally on a mismatch), restarts the stack and waits for the orchestrator to report healthy.
The pending database migrations are applied by the orchestrator itself on start, so no
separate step is needed. Afterwards it names any agent container still running on the old
image — those have to be recreated.

By hand, step by step — check each step before running the next one, and do **not** start
the stack if the image step failed:

```bash
./scripts/backup.sh
git pull
. scripts/lib/abbilder.sh && abbilder_bereitstellen 0
docker compose up -d
# only if the orchestrator came up but the automatic migration run failed:
docker compose exec orchestrator alembic upgrade head
```

A plain `docker compose pull` performs no signature check, so it is not an update route.
See [UPGRADE.md](UPGRADE.md) for the details, the switches and version-specific
instructions.
