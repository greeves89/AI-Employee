# Security Guide

This document covers the security architecture, hardening checklist, network segmentation, user management, audit logging, and compliance considerations for the AI Employee Platform.

---

## Table of Contents

1. [Security Architecture Overview](#security-architecture-overview)
2. [Hardening Checklist](#hardening-checklist)
3. [Network Segmentation](#network-segmentation)
4. [Secret Management](#secret-management)
5. [Agent Security Controls](#agent-security-controls)
6. [Command Approval Workflow](#command-approval-workflow)
7. [Audit Logging](#audit-logging)
8. [TLS / Encryption](#tls--encryption)
9. [User & Access Management](#user--access-management)
10. [Security Monitoring](#security-monitoring)
11. [Incident Response](#incident-response)
12. [Compliance Notes](#compliance-notes)

---

## Security Architecture Overview

The platform is designed with defense-in-depth. Key security layers:

```
Internet
   │
   ▼
[Traefik] ─── TLS termination, rate limiting, security headers
   │
   ├──▶ [Frontend] ─── Next.js, served over HTTPS
   │
   └──▶ [Orchestrator API] ─── JWT auth, input validation
            │
            ├──▶ [PostgreSQL] ─── internal network only, encrypted at rest (optional)
            ├──▶ [Redis] ─── internal network only, no external exposure
            └──▶ [Docker Proxy] ─── allowlist-based API filtering (NOT raw socket)
                      │
                      └──▶ [Agent Containers] ─── isolated networks, no --privileged
```

**Key security decisions:**
- Docker socket is never exposed directly; a filtering proxy enforces an allowlist
- Agents cannot use `--privileged`, `--pid=host`, `--network=host`, or dangerous mounts
- All agent commands requiring elevated risk go through a user approval workflow
- All sudo/sensitive commands are audit-logged to the database
- Secrets are stored as Docker Secrets (not environment variables) in production

---

## Hardening Checklist

### Before Going to Production

**Credentials & Secrets**
- [ ] Generate a strong `DB_PASSWORD` (32+ random characters)
- [ ] Generate a strong `ENCRYPTION_KEY` (Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
- [ ] Generate a strong `API_SECRET_KEY` (32+ random characters)
- [ ] Store all secrets using Docker Secrets (not `.env` in production)
- [ ] Run `./scripts/init-secrets.sh` to initialize Docker Swarm secrets
- [ ] Delete or restrict access to `.env` file on the host

**Network**
- [ ] Bind orchestrator and frontend ports to `127.0.0.1` only (not `0.0.0.0`)
- [ ] Enable firewall: only ports 80 and 443 publicly accessible
- [ ] Verify database port (5432) is NOT publicly accessible
- [ ] Verify Redis port (6379) is NOT publicly accessible

**TLS**
- [ ] HTTPS enabled via Traefik + Let's Encrypt
- [ ] HTTP → HTTPS redirect configured
- [ ] Set `DOMAIN` and `ACME_EMAIL` in `.env`

**Docker**
- [ ] Docker socket NOT directly mounted to any application container
- [ ] Docker proxy allowlist reviewed (`docker-proxy/allowlist.yml`)
- [ ] No containers running with `--privileged`
- [ ] Resource limits set for all containers

**Application**
- [ ] Change default Grafana admin password on first login
- [ ] Confirm `--dangerously-skip-permissions` flag is NOT in use
- [ ] Review agent command blocklist (`agent/app/command_filter.py`)
- [ ] Enable audit logging (enabled by default)

---

## Network Segmentation

Three Docker networks are defined:

| Network | Purpose | External Access |
|---------|---------|----------------|
| `internal` | Core services: postgres, redis, orchestrator, frontend | None |
| `agent-network` | Agent containers ↔ orchestrator, docker-proxy | None |
| (host) | Traefik only | Ports 80, 443 |

**Rules:**
- Agent containers can only reach the orchestrator and docker-proxy, not postgres/redis directly
- The database and Redis have zero external ports bound
- All inter-service communication stays within Docker networks

**Verify network isolation:**
```bash
# Confirm postgres has no public port binding
docker port ai-employee-postgres   # should return nothing

# Confirm redis has no public port binding
docker port ai-employee-redis      # should return nothing

# List networks
docker network ls | grep ai-employee

# Inspect which containers are on each network
docker network inspect ai-employee_internal
docker network inspect ai-employee_agent-network
```

**Firewall setup (UFW example):**
```bash
sudo ufw default deny incoming
sudo ufw allow ssh
sudo ufw allow 80/tcp    # HTTP (redirects to HTTPS)
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable
```

---

## Secret Management

### Development

Use `.env` file (never commit to git):
```bash
cp .env.example .env
# Edit .env with actual values
```

`.env` is in `.gitignore`. Verify it was not accidentally committed:
```bash
git log --all --full-history -- .env
```

### Production (Docker Secrets)

```bash
# Initialize secrets (requires Docker Swarm mode)
docker swarm init
./scripts/init-secrets.sh

# Deploy with secrets
docker compose -f docker-compose.yml -f docker-compose.secrets.yml up -d
```

Secrets are mounted at `/run/secrets/<name>` inside containers, readable only by the container process.

### Secret Rotation

1. Generate new value
2. Update Docker Secret: `printf 'NEW_VALUE' | docker secret create db_password_v2 -`
3. Update `docker-compose.secrets.yml` to reference new secret
4. Redeploy: `docker compose up -d --no-deps orchestrator`
5. Remove old secret: `docker secret rm db_password`

---

## Agent Security Controls

### What Agents Can Do

Agents run as isolated Docker containers with:
- Access only to the `agent-network` (can reach orchestrator, docker-proxy)
- No access to host network, postgres, or redis directly
- Resource limits (CPU, memory) enforced by Docker

### What Agents Cannot Do

The following are blocked at the Docker proxy level (`docker-proxy/allowlist.yml`):
- Creating containers with `--privileged`
- Mounting the host Docker socket (`/var/run/docker.sock`)
- Mounting sensitive host paths (`/etc`, `/root`, `/home`, `/proc`, `/sys`)
- Using `--network=host` or `--pid=host`
- Any container exec not in the allowlist

### Command Filtering

Dangerous shell commands are blocked before execution:
- `rm -rf /` and variants
- `chmod 777 /` and system-wide permission changes
- `dd if=/dev/zero` (disk wipe)
- Fork bombs
- Reverse shells (common patterns)

Review/update the blocklist: `agent/app/command_filter.py`

---

## Command Approval Workflow

High-risk agent actions require explicit user approval before execution.

**Risk levels:**
- `low` — executed automatically (file reads, web searches)
- `medium` — logged, executed with notification
- `high` — requires user approval via UI modal
- `blocked` — never allowed, returns error

**Approval flow:**
1. Agent requests tool execution → orchestrator evaluates risk level
2. If `high`: approval request sent to user via WebSocket notification
3. User sees modal with command preview, reasoning, and risk level
4. User approves or denies
5. Decision is recorded in audit log

**API:** `POST /api/approvals/{request_id}/decision`

**Timeout:** Approval requests expire after 5 minutes (configurable).

---

## Audit Logging

All sensitive actions are logged to the `audit_logs` database table.

### What Is Logged

| Event | Details Captured |
|-------|-----------------|
| Agent command execution | User, agent ID, command, result, timestamp |
| Command approval/denial | User, request ID, decision, reason |
| API key creation/revocation | User, key name, timestamp |
| Authentication failures | IP, user, timestamp, reason |
| Docker operations | Container created/started/stopped, image pulled |

### Querying Audit Logs

```bash
# Recent events (last 100)
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT timestamp, event_type, user_id, details
   FROM audit_logs ORDER BY timestamp DESC LIMIT 100;"

# Filter by event type
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT * FROM audit_logs WHERE event_type = 'COMMAND_EXEC'
   AND timestamp > NOW() - INTERVAL '24 hours';"

# Suspicious activity: failed auth attempts
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT timestamp, details->>'ip' as ip, COUNT(*)
   FROM audit_logs
   WHERE event_type = 'AUTH_FAILURE'
     AND timestamp > NOW() - INTERVAL '1 hour'
   GROUP BY timestamp, ip
   HAVING COUNT(*) > 5;"
```

### Log Retention

Audit logs are stored in PostgreSQL and included in daily backups.
Default retention: logs are kept indefinitely (add a purge policy if required by compliance).

To purge logs older than 1 year:
```bash
docker exec ai-employee-postgres psql -U ai_employee -c \
  "DELETE FROM audit_logs WHERE timestamp < NOW() - INTERVAL '1 year';"
```

---

## TLS / Encryption

### In-Transit Encryption

- External traffic: TLS 1.2+ enforced by Traefik (Let's Encrypt certificates)
- Internal traffic: Docker network isolation (not encrypted by default; use TLS for sensitive data if needed)

### At-Rest Encryption

- Sensitive agent secrets are encrypted using Fernet symmetric encryption (`ENCRYPTION_KEY`)
- Host disk encryption (LUKS / dm-crypt) is recommended for the server volume but is out of scope for this application

### Certificate Management

Certificates are auto-renewed by Traefik/Let's Encrypt (checked daily, renewed at 30 days before expiry).

Check certificate expiry:
```bash
echo | openssl s_client -connect yourdomain.com:443 2>/dev/null | openssl x509 -noout -dates
```

Traefik stores certificates in the `letsencrypt_data` Docker volume.

---

## User & Access Management

### Principle of Least Privilege

- Each service has its own database credentials (not shared superuser)
- Agents cannot access the database directly
- API keys are scoped to specific permissions

### API Key Management

- Rotate API keys every 90 days
- Revoke immediately when a team member leaves
- Never log or expose API keys in responses

```bash
# List active API keys (via database)
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT id, name, created_at, last_used_at, revoked_at
   FROM api_keys WHERE revoked_at IS NULL;"

# Revoke a specific key
docker exec ai-employee-postgres psql -U ai_employee -c \
  "UPDATE api_keys SET revoked_at = NOW() WHERE id = '<key-id>';"
```

### Multi-Factor Authentication

MFA is encouraged for all administrator accounts. Configure via the web UI under Settings → Security.

---

## Security Monitoring

### Alerts to Configure

Beyond the default infrastructure alerts (`monitoring/alerts.yml`), consider:

1. **Multiple authentication failures from same IP** — potential brute force
2. **Agent container running for > 1 hour** — potential runaway task
3. **Unusual number of `blocked` commands** — potential prompt injection attempt
4. **New API key created** — notify admins immediately

### Log Patterns to Watch

```bash
# Authentication failures
docker logs ai-employee-orchestrator 2>&1 | grep -i "auth.*fail\|unauthorized\|403"

# Blocked commands
docker logs ai-employee-orchestrator 2>&1 | grep -i "blocked\|command_filter"

# Docker proxy rejections
docker logs ai-employee-docker-proxy 2>&1 | grep -i "denied\|blocked\|forbidden"
```

### Dependency Scanning

Periodically scan for known vulnerabilities in dependencies:

```bash
# For Node.js packages
cd frontend && npm audit

# For Python packages
pip-audit -r orchestrator/requirements.txt

# Docker image vulnerabilities
docker scout cves ai-employee-orchestrator:latest
```

---

## Incident Response

### Suspected Compromise

1. **Isolate:** Stop all agent containers immediately
   ```bash
   docker ps --filter "name=agent-" -q | xargs docker stop
   ```

2. **Preserve evidence:** Dump logs before restarting anything
   ```bash
   docker compose logs > /tmp/incident-$(date +%Y%m%d).log
   docker exec ai-employee-postgres pg_dump -U ai_employee > /tmp/db-incident.sql
   ```

3. **Revoke credentials:** Rotate all API keys and secrets

4. **Review audit logs:** Identify the attack vector and scope

5. **Restore from clean backup** if compromise is confirmed

### Lost Admin Access

If the admin account is locked out:
```bash
# Reset admin password via database
docker exec ai-employee-postgres psql -U ai_employee -c \
  "UPDATE users SET password_hash = '<new-bcrypt-hash>' WHERE role = 'admin' LIMIT 1;"
```

Generate bcrypt hash: `python3 -c "import bcrypt; print(bcrypt.hashpw(b'newpassword', bcrypt.gensalt()).decode())"`

---

## Compliance Notes

### Data Storage

- All user data is stored in PostgreSQL within the Docker environment
- No data is sent to third parties except:
  - Conversation content sent to Anthropic API (for agent LLM processing)
  - TLS certificate domain sent to Let's Encrypt

### GDPR Considerations

- Users can export their data via the web UI (Settings → Export Data)
- To delete a user's data: `DELETE FROM users WHERE id = '<user-id>';` (cascades to related records)
- Review data retention policies for audit logs and conversation history

### API Key Security Standards

- Keys are stored hashed (SHA-256) in the database — plaintext is never stored
- Keys are shown only once at creation time

### Security Contact

To report a security vulnerability, open a private security advisory via GitHub:
`Settings → Security → Advisories → New draft security advisory`
