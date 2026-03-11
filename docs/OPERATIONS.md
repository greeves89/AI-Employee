# Operations Manual

This guide covers day-to-day operations: starting and stopping services, upgrades, backups, monitoring, and common maintenance tasks.

---

## Table of Contents

1. [Service Management](#service-management)
2. [Backup & Restore](#backup--restore)
3. [Monitoring](#monitoring)
4. [Log Management](#log-management)
5. [Upgrade Process](#upgrade-process)
6. [User Management](#user-management)
7. [Configuration Reference](#configuration-reference)
8. [Routine Maintenance](#routine-maintenance)

---

## Service Management

### Start Services

**Development:**
```bash
docker compose up -d
```

**Production (with TLS via Traefik):**
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

**With monitoring stack:**
```bash
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d
```

### Stop Services

```bash
# Graceful stop (containers remain, data preserved)
docker compose down

# Stop and remove containers + networks (data volumes preserved)
docker compose down --remove-orphans

# Stop everything including volumes (DESTRUCTIVE - deletes all data)
docker compose down -v   # WARNING: only for dev reset
```

### Restart a Single Service

```bash
docker compose restart orchestrator
docker compose restart frontend
docker compose restart postgres
```

### Check Service Status

```bash
# All services at once
docker compose ps

# Health status details
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Health}}"

# Health check via API
curl http://localhost:8000/health
```

### View Running Agents

```bash
# List all agent containers
docker ps --filter "name=agent-" --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}"
```

---

## Backup & Restore

### Automated Backups

Backups run daily via cron (installed by `scripts/install-backup-cron.sh`).

**Backup schedule:**
- Daily backups: retained for 7 days
- Weekly backups (Sunday): retained for 4 weeks
- Backup location: `/var/backups/ai-employee/`

**Check cron job:**
```bash
crontab -l | grep backup
```

**View backup log:**
```bash
tail -f /var/backups/ai-employee/backup.log
```

### Manual Backup

```bash
# Run backup now
./scripts/backup.sh

# Backup to custom directory
BACKUP_DIR=/mnt/external ./scripts/backup.sh
```

Backup contents:
- `postgres_dump.sql.gz` — full database dump
- `volumes/postgres_data.tar.gz` — PostgreSQL data directory
- `volumes/redis_data.tar.gz` — Redis AOF data
- `volumes/letsencrypt_data.tar.gz` — TLS certificates

### Restore from Backup

```bash
# Restore from most recent backup
./scripts/restore.sh

# Restore from specific backup
./scripts/restore.sh /var/backups/ai-employee/daily/20260219_030000

# Restore database only (skips volume restore)
./scripts/restore.sh --db-only /var/backups/ai-employee/daily/20260219_030000
```

**Restore procedure:**
1. Stop services: `docker compose down`
2. Run restore script
3. Start services: `docker compose up -d`
4. Verify: `curl http://localhost:8000/health`

### Verify Backup Integrity

```bash
# Test database dump is readable
gunzip -c /var/backups/ai-employee/daily/TIMESTAMP/postgres_dump.sql.gz | head -20

# List backup contents
ls -lh /var/backups/ai-employee/daily/
ls -lh /var/backups/ai-employee/weekly/
```

---

## Monitoring

### Access Dashboards

| Service | URL | Default Credentials |
|---------|-----|---------------------|
| Grafana | http://localhost:3001 | admin / admin (change on first login) |
| Prometheus | http://localhost:9090 | — |
| Loki | (via Grafana) | — |

Start monitoring stack:
```bash
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d
```

### Key Metrics to Watch

**System health:**
- CPU usage < 80% sustained
- RAM usage < 85%
- Disk usage < 80% (triggers alert at 85%)

**Application metrics (via Grafana):**
- API response time p95 < 500ms
- Task queue depth (should drain within minutes)
- Agent container count and status
- WebSocket connection count

**Database:**
- PostgreSQL connection pool utilization
- Query latency
- Replication lag (if replicas configured)

### Alerts

Alerts are defined in `monitoring/alerts.yml`. Active alerts appear in:
1. Grafana → Alerting → Alert Rules
2. Prometheus → Alerts tab

**Default alert thresholds:**
- Disk > 85%: warning; > 95%: critical
- RAM > 90%: warning
- Service down for > 2 minutes: critical
- Postgres down: critical
- Redis down: critical

### Check Service Health via CLI

```bash
# Orchestrator health endpoint
curl -s http://localhost:8000/health | python3 -m json.tool

# Database connectivity
docker exec ai-employee-postgres pg_isready -U ai_employee

# Redis connectivity
docker exec ai-employee-redis redis-cli ping

# Check recent errors in orchestrator
docker logs ai-employee-orchestrator --since 1h 2>&1 | grep -i error
```

---

## Log Management

### View Logs

```bash
# All services, last 100 lines
docker compose logs --tail=100

# Follow specific service
docker compose logs -f orchestrator
docker compose logs -f frontend

# Agent container logs (replace with actual container name)
docker logs agent-<id> --tail=50

# Audit logs (sudo command history)
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT timestamp, user_id, command, result FROM audit_logs ORDER BY timestamp DESC LIMIT 20;"
```

### Log Aggregation (Loki)

When the monitoring stack is running, all container logs are collected by Loki and queryable in Grafana (Explore → Loki).

Example LogQL queries:
```
{container="ai-employee-orchestrator"} |= "error"
{container=~"agent-.*"} | json | level="error"
{container="ai-employee-postgres"} |= "FATAL"
```

### Log Rotation

Docker's default log rotation is configured in `/etc/docker/daemon.json`:
```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  }
}
```

Apply changes: `sudo systemctl restart docker`

---

## Upgrade Process

### Pre-Upgrade Checklist

- [ ] Create a full backup: `./scripts/backup.sh`
- [ ] Check the [CHANGELOG](../CHANGELOG.md) for breaking changes
- [ ] Notify users of maintenance window
- [ ] Verify disk space: at least 2x current image size free

### Standard Upgrade (no breaking changes)

```bash
# 1. Pull latest code
git pull origin main

# 2. Build new images
docker compose build

# 3. Restart services (one at a time to minimize downtime)
docker compose up -d --no-deps orchestrator
docker compose up -d --no-deps frontend

# 4. Verify health
curl http://localhost:8000/health
```

### Database Migration

If new migrations are included:
```bash
# Migrations run automatically on orchestrator startup.
# Watch logs to confirm:
docker compose logs -f orchestrator | grep -i migration
```

### Rollback Procedure

If the upgrade fails:
```bash
# 1. Stop new containers
docker compose down

# 2. Restore from backup taken before upgrade
./scripts/restore.sh /var/backups/ai-employee/daily/PRE_UPGRADE_TIMESTAMP

# 3. Checkout previous version
git checkout <previous-tag>

# 4. Rebuild and start
docker compose build
docker compose up -d
```

---

## User Management

### Create API Key (for agent access)

Via the web UI: Settings → API Keys → Generate New Key

Via CLI:
```bash
docker exec ai-employee-orchestrator python -m app.cli create-api-key --name "automation"
```

### Revoke API Key

Via web UI: Settings → API Keys → Revoke

### View Active Sessions

```bash
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT id, user_id, created_at, last_seen FROM sessions WHERE expires_at > NOW();"
```

### Flush All Sessions (force re-login)

```bash
docker exec ai-employee-postgres psql -U ai_employee -c \
  "DELETE FROM sessions;"
docker compose restart orchestrator
```

---

## Configuration Reference

### Environment Variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required. Anthropic API key or OAuth token |
| `DB_PASSWORD` | `devpassword` | PostgreSQL password (change in production!) |
| `ENCRYPTION_KEY` | — | Fernet key for encrypting secrets at rest |
| `API_SECRET_KEY` | — | JWT signing key for API authentication |
| `DOMAIN` | — | Production domain (e.g., `ai.example.com`) |
| `ACME_EMAIL` | — | Email for Let's Encrypt TLS certificates |
| `CLAUDE_MODEL` | `claude-opus-4-6` | Model used by agents |

### Production Secrets (Docker Secrets)

Production deployments should use Docker Secrets instead of environment variables:
```bash
./scripts/init-secrets.sh   # Initialize secrets in Docker Swarm
docker compose -f docker-compose.yml -f docker-compose.secrets.yml up -d
```

### Resource Limits

Default resource limits (from `docker-compose.yml`):

| Service | Memory Limit |
|---------|-------------|
| postgres | 512 MB |
| redis | 256 MB |
| orchestrator | 512 MB |
| frontend | 256 MB |
| docker-proxy | 128 MB |
| traefik | 128 MB |

Adjust in `docker-compose.yml` under `deploy.resources.limits`.

---

## Routine Maintenance

### Weekly Tasks

- [ ] Review Grafana dashboards for anomalies
- [ ] Check disk usage: `df -h`
- [ ] Verify backups are completing: `tail /var/backups/ai-employee/backup.log`
- [ ] Review audit logs for unusual commands

### Monthly Tasks

- [ ] Rotate API keys that are > 90 days old
- [ ] Check for security updates: `docker compose pull` and rebuild
- [ ] Review and prune unused agent containers: `docker container prune`
- [ ] Clean up old Docker images: `docker image prune -a --filter "until=720h"`
- [ ] Test backup restore procedure in staging

### Disk Cleanup

```bash
# Remove stopped containers
docker container prune -f

# Remove unused images (keep last 30 days)
docker image prune -a --filter "until=720h" -f

# Remove unused volumes (CAUTION: verify before running)
docker volume ls
docker volume prune -f   # Only removes volumes not attached to any container

# Check Docker disk usage
docker system df
```

### Database Maintenance

```bash
# Run VACUUM ANALYZE to reclaim space and update statistics
docker exec ai-employee-postgres psql -U ai_employee -c "VACUUM ANALYZE;"

# Check database size
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT pg_size_pretty(pg_database_size('ai_employee'));"

# Check table sizes
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
   FROM pg_catalog.pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 10;"
```
