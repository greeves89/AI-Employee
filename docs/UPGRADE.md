# Upgrade Guide

This document describes how to upgrade the AI Employee Platform, including pre-upgrade preparation, the upgrade procedure, and rollback steps.

---

## Table of Contents

1. [Version Compatibility](#version-compatibility)
2. [Pre-Upgrade Checklist](#pre-upgrade-checklist)
3. [Standard Upgrade Procedure](#standard-upgrade-procedure)
4. [Database Migrations](#database-migrations)
5. [Configuration Changes](#configuration-changes)
6. [Rollback Procedure](#rollback-procedure)
7. [Upgrading Dependencies](#upgrading-dependencies)
8. [Zero-Downtime Upgrade (Advanced)](#zero-downtime-upgrade-advanced)

---

## Version Compatibility

Always check the [CHANGELOG](../CHANGELOG.md) and release notes before upgrading. Pay special attention to:

- **Breaking changes** — API or configuration changes that require manual action
- **Database migration requirements** — New migrations that will run on startup
- **New required environment variables** — New settings that must be added to `.env`
- **Deprecated features** — Features scheduled for removal

**Supported upgrade paths:** Always upgrade one minor version at a time. Skipping versions (e.g., v1.0 → v3.0) is not supported and may cause migration failures.

---

## Pre-Upgrade Checklist

Complete all steps before starting the upgrade:

### 1. Create a Full Backup

```bash
./scripts/backup.sh
```

Verify the backup completed successfully:
```bash
tail -5 /var/backups/ai-employee/backup.log
ls -lh /var/backups/ai-employee/daily/$(date +%Y%m%d)*/
```

Note the backup timestamp — you'll need it if rollback is required.

### 2. Check Current Version

```bash
# Git tag of current deployment
git describe --tags --abbrev=0

# Or check running image labels
docker inspect ai-employee-orchestrator | grep -i version
```

### 3. Review the Changelog

```bash
# Pull release notes without applying changes
git fetch origin
git log HEAD..origin/main --oneline
git diff HEAD..origin/main -- CHANGELOG.md
```

Look for any `BREAKING CHANGE` or `MIGRATION REQUIRED` notes.

### 4. Check Disk Space

You need at least 2x the current image size free for the build:
```bash
df -h
docker system df
```

Free space if needed:
```bash
docker image prune -a --filter "until=720h" -f
```

### 5. Notify Users

If running in a shared environment, notify users of the maintenance window before proceeding.

### 6. Drain Active Agent Tasks

```bash
# See running tasks
docker exec ai-employee-postgres psql -U ai_employee -c \
  "SELECT id, status, created_at FROM tasks WHERE status IN ('pending', 'running');"

# Wait for tasks to complete, or cancel them:
docker exec ai-employee-postgres psql -U ai_employee -c \
  "UPDATE tasks SET status = 'cancelled' WHERE status = 'pending';"
```

---

## Standard Upgrade Procedure

### Step 1: Pull Latest Code

```bash
git pull origin main

# Or pull a specific version tag:
git fetch --tags
git checkout v2.1.0
```

### Step 2: Review Configuration Changes

Check if `.env.example` has new variables:
```bash
git diff HEAD~1 .env.example
```

Add any new required variables to your `.env` file before continuing.

### Step 3: Build New Images

```bash
docker compose build --no-cache
```

The `--no-cache` flag ensures you get fresh builds without stale layers.

### Step 4: Stop Current Services

```bash
docker compose down
```

This gracefully stops all containers. Data volumes are preserved.

### Step 5: Start Updated Services

```bash
# Development
docker compose up -d

# Production
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Database migrations run automatically on orchestrator startup.

### Step 6: Verify the Upgrade

```bash
# Watch startup logs
docker compose logs -f orchestrator --until=60s

# Check health
curl -s http://localhost:8000/health | python3 -m json.tool

# Confirm new version
docker inspect ai-employee-orchestrator | grep -i "version\|created"

# Run a quick smoke test
curl -s http://localhost:8000/health | grep '"status":"healthy"'
echo "✓ Health check passed"
```

### Step 7: Test Core Functionality

Manually verify:
- [ ] Can log in via the web UI
- [ ] Can create a new agent
- [ ] Agent responds to a chat message
- [ ] Existing conversations are accessible

---

## Database Migrations

Migrations are managed with Alembic and run automatically when the orchestrator starts.

### Monitor Migration Progress

```bash
docker compose logs -f orchestrator | grep -i "alembic\|migration\|upgrade"
```

A successful migration looks like:
```
INFO  [alembic.runtime.migration] Running upgrade abc123 -> def456, add audit_logs table
INFO  [alembic.runtime.migration] Running upgrade def456 -> ghi789, add schedules
INFO  [alembic.runtime.migration] Done.
```

### Manual Migration (if auto-run fails)

```bash
# Run migrations manually
docker exec ai-employee-orchestrator alembic upgrade head

# Check current migration version
docker exec ai-employee-orchestrator alembic current

# See pending migrations
docker exec ai-employee-orchestrator alembic show head
```

### Migration Fails

If a migration fails partway through:

1. **Do not restart the orchestrator repeatedly** — this may cause a partially-applied migration to be retried.
2. Check the error:
   ```bash
   docker logs ai-employee-orchestrator 2>&1 | grep -A 20 "alembic\|ERROR"
   ```
3. If the migration is idempotent, fix the underlying issue (e.g., disk space) and retry:
   ```bash
   docker exec ai-employee-orchestrator alembic upgrade head
   ```
4. If the migration cannot be retried, restore from backup (see [Rollback Procedure](#rollback-procedure)).

---

## Configuration Changes

### New Environment Variables

When new variables are added in a release, they have defaults that preserve backward compatibility. However, review them and set explicit values in `.env`:

```bash
# See what changed in .env.example
git diff PREVIOUS_TAG..NEW_TAG -- .env.example
```

### Changed Default Values

If a default value changed between versions, your deployment will adopt the new default on restart unless you have the variable explicitly set in `.env`. Always diff `.env.example` before upgrading.

### Removed Configuration

If a configuration variable was removed, it's safe to leave it in `.env` — it will simply be ignored.

---

## Rollback Procedure

If the upgrade causes issues, roll back to the previous version.

### Step 1: Stop Services

```bash
docker compose down
```

### Step 2: Restore Database from Pre-Upgrade Backup

```bash
# Use the backup timestamp you noted in the pre-upgrade checklist
./scripts/restore.sh /var/backups/ai-employee/daily/PRE_UPGRADE_TIMESTAMP
```

This restores both the database and Docker volumes.

### Step 3: Checkout Previous Version

```bash
# Return to previous git tag
git checkout PREVIOUS_TAG

# Or return to the commit before the upgrade
git checkout HEAD~1
```

### Step 4: Rebuild and Start

```bash
docker compose build
docker compose up -d
```

### Step 5: Verify Rollback

```bash
curl http://localhost:8000/health
# Check version matches the previous version
```

### Step 6: Report the Issue

Open a GitHub issue with:
- The version you upgraded from and to
- The error logs from the failed upgrade
- Steps to reproduce

---

## Upgrading Dependencies

### Docker Images (Base Images)

To get security patches in base images without a full version upgrade:

```bash
# Pull latest base images
docker compose pull

# Rebuild services that depend on pulled images
docker compose build --no-cache

# Rolling restart
docker compose up -d --no-deps orchestrator
docker compose up -d --no-deps frontend
```

### Python Dependencies (Orchestrator)

```bash
# Inside the orchestrator container
docker exec ai-employee-orchestrator pip list --outdated

# Update all dependencies (test in dev first!)
docker exec ai-employee-orchestrator pip install --upgrade -r requirements.txt
```

To make dependency updates permanent, update `orchestrator/requirements.txt` and rebuild the image.

### Node.js Dependencies (Frontend)

```bash
cd frontend
npm outdated
npm update
# For major version updates:
npm install <package>@latest
cd ..
docker compose build frontend
```

### Security Patch Workflow

For urgent security patches:
1. Update the specific dependency in `requirements.txt` or `package.json`
2. Rebuild the affected service: `docker compose build --no-cache <service>`
3. Restart: `docker compose up -d --no-deps <service>`
4. No backup/migration needed for dependency-only changes

---

## Zero-Downtime Upgrade (Advanced)

For production environments where downtime is not acceptable, use a blue-green or rolling upgrade strategy.

### Rolling Upgrade (Single Host)

Restart services one at a time to minimize downtime:

```bash
# 1. Build new images (while old ones still serve traffic)
docker compose build

# 2. Update orchestrator (brief WebSocket disconnects, clients reconnect)
docker compose up -d --no-deps orchestrator

# 3. Wait for health
until curl -sf http://localhost:8000/health; do sleep 2; done
echo "Orchestrator healthy"

# 4. Update frontend
docker compose up -d --no-deps frontend
```

**Note:** Database migrations still require a brief stop of the orchestrator. For truly zero-downtime migrations, write backward-compatible migrations and separate the migration step from the deployment.

### Backward-Compatible Migration Pattern

1. **Release N:** Add new column with a default value (old code ignores it)
2. **Release N+1:** Start writing to new column (old code still works)
3. **Release N+2:** Remove old column (old code no longer deployed)

This avoids the need for maintenance windows during database schema changes.
