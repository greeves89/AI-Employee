# Troubleshooting Guide

This guide covers common issues, how to diagnose them, and steps to resolve them.

---

## Table of Contents

1. [Quick Diagnostics](#quick-diagnostics)
2. [Services Won't Start](#services-wont-start)
3. [Agent Issues](#agent-issues)
4. [Authentication Problems](#authentication-problems)
5. [Database Issues](#database-issues)
6. [WebSocket / Real-Time Issues](#websocket--real-time-issues)
7. [Performance Issues](#performance-issues)
8. [Backup / Restore Failures](#backup--restore-failures)
9. [TLS / HTTPS Issues](#tls--https-issues)
10. [Log Locations](#log-locations)
11. [Debug Commands Reference](#debug-commands-reference)

---

## Quick Diagnostics

Run this first to get an overall health snapshot:

```bash
# Service status
docker compose ps

# Health endpoint (should return 200 with all checks green)
curl -s http://localhost:8000/health | python3 -m json.tool

# Recent errors across all services
docker compose logs --tail=50 2>&1 | grep -i "error\|exception\|fatal\|critical"

# Resource usage
docker stats --no-stream

# Disk space
df -h
```

---

## Services Won't Start

### PostgreSQL fails to start

**Symptom:** `ai-employee-postgres` exits immediately or stays in "starting" state.

**Check logs:**
```bash
docker logs ai-employee-postgres --tail=30
```

**Common causes and fixes:**

| Error in logs | Cause | Fix |
|---------------|-------|-----|
| `FATAL: data directory "/var/lib/postgresql/data" has wrong ownership` | Volume permissions issue | `docker volume rm ai-employee_postgres_data` and recreate |
| `FATAL: password authentication failed` | Wrong `DB_PASSWORD` in `.env` | Correct `DB_PASSWORD` and restart |
| `port 5432 is already in use` | Another PostgreSQL running on host | Stop it: `sudo systemctl stop postgresql` |
| `could not translate host name` | DNS issue in container | Restart Docker: `sudo systemctl restart docker` |

### Orchestrator fails to start

**Check logs:**
```bash
docker logs ai-employee-orchestrator --tail=50
```

**Common causes:**

| Error | Fix |
|-------|-----|
| `Could not connect to database` | Ensure postgres is healthy first: `docker compose up -d postgres && sleep 5 && docker compose up -d orchestrator` |
| `ENCRYPTION_KEY missing or invalid` | Set a valid Fernet key in `.env` |
| `Module not found` | Rebuild: `docker compose build orchestrator` |
| `Alembic migration failed` | Check migration logs; may need manual rollback |

### Frontend fails to start

```bash
docker logs ai-employee-frontend --tail=30
```

| Error | Fix |
|-------|-----|
| `NEXT_PUBLIC_API_URL not set` | Set in `.env` and rebuild: `docker compose build frontend` |
| `Error: Cannot find module` | Rebuild: `docker compose build frontend` |
| Port 3000 already in use | Stop conflicting process: `lsof -i :3000` |

### Docker Proxy fails to start

```bash
docker logs ai-employee-docker-proxy --tail=30
```

| Error | Fix |
|-------|-----|
| `Permission denied: /var/run/docker.sock` | Add current user to docker group or run with sudo |
| `FileNotFoundError: allowlist.yml` | Verify `docker-proxy/allowlist.yml` exists |

---

## Agent Issues

### Agent container won't spawn

**Symptom:** Creating an agent in the UI fails or the agent stays "pending" indefinitely.

**Check orchestrator logs:**
```bash
docker logs ai-employee-orchestrator 2>&1 | grep -i "agent\|docker\|spawn" | tail -20
```

**Check docker proxy logs:**
```bash
docker logs ai-employee-docker-proxy --tail=20
```

**Common causes:**

| Cause | Fix |
|-------|-----|
| Docker socket not accessible | Verify proxy is running: `docker compose ps docker-proxy` |
| Image not pulled | Pre-pull: `docker pull ghcr.io/anthropics/claude-code:latest` |
| Allowlist blocking operation | Check proxy logs for "denied"; update `docker-proxy/allowlist.yml` if legitimate |
| Host out of memory | `docker stats --no-stream`; stop unused agents |

### Agent is stuck / not responding

**Symptom:** Chat messages sent but no response appears.

```bash
# Find the agent container
docker ps --filter "name=agent-"

# Check its logs
docker logs agent-<id> --tail=30

# Check Redis pub/sub is working
docker exec ai-employee-redis redis-cli PUBSUB CHANNELS
```

**Common causes:**
- Anthropic API rate limit hit → wait and retry; check API key quota
- Agent container OOM-killed → increase memory limit in agent template
- Redis connection lost → restart Redis: `docker compose restart redis`

### Agent produces no output (silent failure)

```bash
# Check if Claude API key is valid
docker exec ai-employee-orchestrator env | grep ANTHROPIC

# Test API connectivity from orchestrator
docker exec ai-employee-orchestrator python -c "import anthropic; c = anthropic.Anthropic(); print(c.models.list())"
```

### Approval modal not appearing

**Symptom:** Agent should request approval but UI doesn't show the modal.

```bash
# Check WebSocket connection in browser: DevTools → Network → WS
# Look for messages with type "approval_request"

# Check Redis for pending approvals
docker exec ai-employee-redis redis-cli KEYS "approval:*"

# Check orchestrator logs
docker logs ai-employee-orchestrator 2>&1 | grep -i "approval" | tail -20
```

---

## Authentication Problems

### Cannot log in (OAuth)

**Symptom:** OAuth redirect fails or returns error.

```bash
# Check orchestrator logs for OAuth errors
docker logs ai-employee-orchestrator 2>&1 | grep -i "oauth\|auth\|token" | tail -20
```

Common issues:
- `OAUTH_REDIRECT_BASE_URL` not set or wrong → update `.env`
- OAuth app callback URL mismatch → update in your OAuth provider settings
- Clock skew > 5 minutes → sync host time: `sudo timedatectl set-ntp true`

### JWT token expired

**Symptom:** API returns 401 after being logged in.

The frontend handles token refresh automatically. If this persists:
1. Log out and log back in
2. Check that `API_SECRET_KEY` hasn't changed (changing it invalidates all sessions)

### API key authentication fails

```bash
# Test API key
curl -H "Authorization: Bearer YOUR_API_KEY" http://localhost:8000/api/agents

# Check if key is revoked
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT id, name, revoked_at FROM api_keys WHERE id = 'YOUR_KEY_ID';"
```

---

## Database Issues

### Migration errors on startup

```bash
docker logs ai-employee-orchestrator 2>&1 | grep -i "alembic\|migration"
```

**To manually run migrations:**
```bash
docker exec ai-employee-orchestrator alembic upgrade head
```

**To see migration history:**
```bash
docker exec ai-employee-orchestrator alembic history
```

**If migration is stuck (locked):**
```bash
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT * FROM alembic_version;"

# Check for locks
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT pid, query, state FROM pg_stat_activity WHERE state != 'idle';"

# Kill blocking query if needed
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid();"
```

### Database connection pool exhausted

**Symptom:** `TimeoutError: QueuePool limit of size X overflow Y reached`

```bash
# Check active connections
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT count(*) FROM pg_stat_activity WHERE datname = 'ai_employee';"

# Restart orchestrator to reset pool
docker compose restart orchestrator
```

Long-term fix: Tune `DB_POOL_SIZE` and `DB_MAX_OVERFLOW` in orchestrator config.

### Database disk full

```bash
# Check PostgreSQL volume size
docker system df -v | grep postgres

# Run VACUUM to reclaim space
docker exec ai-employee-postgres psql -U ai_employee -c "VACUUM FULL;"

# Find large tables
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
   FROM pg_catalog.pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 5;"
```

---

## WebSocket / Real-Time Issues

### Chat messages not streaming

**Symptom:** Send a message, spinner spins but no text appears.

1. **Check browser console** for WebSocket errors (DevTools → Console)
2. **Verify WebSocket connection:** DevTools → Network → WS tab — should see an active connection
3. **Check Redis pub/sub:**
   ```bash
   docker exec ai-employee-redis redis-cli SUBSCRIBE test-channel
   # In another terminal: docker exec ai-employee-redis redis-cli PUBLISH test-channel "hello"
   # Should see "hello" in subscriber
   ```

### WebSocket disconnects frequently

**Symptom:** Chat reconnects every few minutes; messages are lost.

Common causes:
- Traefik WebSocket timeout → add to Traefik config:
  ```yaml
  # traefik/traefik.yml
  serversTransport:
    forwardingTimeouts:
      responseHeaderTimeout: 0s  # 0 = no timeout
  ```
- Nginx/load balancer timeout (if using custom proxy)
- Network instability on client

### Messages appear out of order

```bash
# Check Redis message ordering
docker logs ai-employee-orchestrator 2>&1 | grep -i "stream\|websocket\|message" | tail -30
```

This usually indicates a bug; open an issue with the logs.

---

## Performance Issues

### High response latency

```bash
# Check API response times
curl -w "%{time_total}s\n" -o /dev/null -s http://localhost:8000/health

# Check database query times
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT query, mean_exec_time, calls
   FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10;"

# Check Redis latency
docker exec ai-employee-redis redis-cli --latency -i 1
```

### High memory usage

```bash
# Per-container memory usage
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}"

# PostgreSQL memory
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT pg_size_pretty(sum(size)) FROM pg_buffercache;"
```

Tune `shared_buffers` and `work_mem` in PostgreSQL if needed.

### Task queue not draining

```bash
# Check Redis queue depth
docker exec ai-employee-redis redis-cli LLEN task_queue

# Check for stuck tasks in database
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT id, status, created_at, agent_id FROM tasks
   WHERE status = 'running' AND created_at < NOW() - INTERVAL '30 minutes';"
```

If tasks are stuck as "running" but agents are gone, reset them:
```bash
docker exec ai-employee-postgres psql -U ai_employee -c \
  "UPDATE tasks SET status = 'failed', error = 'agent lost'
   WHERE status = 'running' AND created_at < NOW() - INTERVAL '1 hour';"
```

---

## Backup / Restore Failures

### Backup script fails

```bash
# Check the backup log
tail -50 /var/backups/ai-employee/backup.log

# Run manually with verbose output
bash -x ./scripts/backup.sh
```

Common causes:
- PostgreSQL container not running → start it first
- No disk space → `df -h /var/backups`
- Wrong `DB_PASSWORD` → check `.env`

### Restore fails: "database exists"

```bash
# Drop and recreate database before restore
docker exec ai-employee-postgres psql -U postgres -c "DROP DATABASE ai_employee;"
docker exec ai-employee-postgres psql -U postgres -c "CREATE DATABASE ai_employee OWNER ai_employee;"
./scripts/restore.sh /var/backups/ai-employee/daily/TIMESTAMP
```

---

## TLS / HTTPS Issues

### Certificate not issued (Let's Encrypt)

```bash
# Check Traefik logs for ACME errors
docker logs ai-employee-traefik 2>&1 | grep -i "acme\|certificate\|error" | tail -20
```

Common causes:
- Port 80 not accessible from internet (Let's Encrypt HTTP challenge requires it)
- `DOMAIN` doesn't resolve to this server's IP
- Rate limit hit (5 certs per domain per week) — use staging first

**Test with staging (no rate limits):**
```yaml
# traefik/traefik.yml
certificatesResolvers:
  letsencrypt:
    acme:
      caServer: "https://acme-staging-v02.api.letsencrypt.org/directory"
```

### Certificate expired

```bash
# Check expiry
echo | openssl s_client -connect yourdomain.com:443 2>/dev/null | openssl x509 -noout -dates

# Force Traefik to renew (delete stored cert)
docker volume inspect letsencrypt_data
# Stop Traefik, edit the acme.json to remove the cert, restart
docker compose restart traefik
```

---

## Log Locations

| Service | How to access logs |
|---------|-------------------|
| Orchestrator | `docker logs ai-employee-orchestrator` |
| Frontend | `docker logs ai-employee-frontend` |
| PostgreSQL | `docker logs ai-employee-postgres` |
| Redis | `docker logs ai-employee-redis` |
| Docker Proxy | `docker logs ai-employee-docker-proxy` |
| Traefik | `docker logs ai-employee-traefik` |
| Agent container | `docker logs agent-<id>` |
| Backup cron | `/var/backups/ai-employee/backup.log` |
| Audit log (DB) | `SELECT * FROM audit_logs ORDER BY timestamp DESC;` |
| All (Loki) | Grafana → Explore → Loki datasource |

---

## Debug Commands Reference

```bash
# === Container management ===
docker compose ps                          # Service status
docker compose logs -f <service>           # Follow logs
docker compose restart <service>           # Restart one service
docker exec -it ai-employee-orchestrator bash  # Shell into orchestrator

# === Health checks ===
curl http://localhost:8000/health          # API health
curl http://localhost:8000/api/agents      # List agents (requires auth)
docker exec ai-employee-postgres pg_isready -U ai_employee
docker exec ai-employee-redis redis-cli ping

# === Database ===
docker exec -it ai-employee-postgres psql -U ai_employee
\dt                                        # List tables
\q                                         # Quit

# === Redis ===
docker exec -it ai-employee-redis redis-cli
KEYS *                                     # List all keys
LLEN task_queue                            # Queue depth
PUBSUB CHANNELS                            # Active pub/sub channels
QUIT

# === Resource usage ===
docker stats --no-stream                   # One-time resource snapshot
docker system df                           # Docker disk usage
df -h                                      # Host disk usage

# === Network debugging ===
docker network ls                          # List networks
docker network inspect ai-employee_internal  # Inspect network
docker exec ai-employee-orchestrator curl http://postgres:5432  # Test internal connectivity

# === Useful one-liners ===
# Stop all agent containers
docker ps --filter "name=agent-" -q | xargs -r docker stop

# Remove exited agent containers
docker ps -a --filter "name=agent-" --filter "status=exited" -q | xargs -r docker rm

# Export database for inspection
docker exec ai-employee-postgres pg_dump -U ai_employee | gzip > /tmp/db-debug.sql.gz

# Get orchestrator Python traceback
docker logs ai-employee-orchestrator 2>&1 | grep -A 10 "Traceback"
```

---

## Getting More Help

1. **Check existing issues:** Search the GitHub repository issues
2. **Enable debug logging:** Set `LOG_LEVEL=DEBUG` in `.env` and restart orchestrator
3. **Collect diagnostics:** Run `./scripts/collect-diagnostics.sh` (if available) and attach to your issue
4. **Community:** Open a GitHub issue with logs, environment info, and reproduction steps
