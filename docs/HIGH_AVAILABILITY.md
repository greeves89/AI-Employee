# High Availability Setup

This guide describes deploying AI Employee in a High Availability (HA) configuration with automatic failover for PostgreSQL, Redis, and the orchestrator service.

## Architecture Overview

```
                         ┌─────────────────────────────────┐
                         │           Traefik LB             │
                         │      (HTTPS + round-robin)       │
                         └──────────┬──────────┬────────────┘
                                    │          │
                         ┌──────────▼──┐  ┌────▼─────────┐
                         │ orchestrator│  │ orchestrator  │
                         │  replica 1  │  │   replica 2   │
                         └──────────┬──┘  └────┬──────────┘
                                    │          │
                  ┌─────────────────┼──────────┘
                  │                 │
          ┌───────▼──────┐  ┌───────▼──────────────────────┐
          │  PgBouncer   │  │        Redis Sentinel         │
          │(conn pooler) │  │  (sentinel-1/2/3: quorum=2)  │
          └───────┬──────┘  └───────┬──────────────────────┘
                  │                 │ monitors
       ┌──────────▼──┐     ┌────────▼──────┐  ┌────────────────┐
       │  PostgreSQL │     │     Redis     │  │  Redis Replica │
       │   Primary   │────▶│   Primary     │──▶│                │
       ├─────────────┤     └───────────────┘  └────────────────┘
       │  Streaming  │
       │ Replication │
       └──────┬──────┘
              │
     ┌────────▼────────┐
     │   PostgreSQL    │
     │     Replica     │
     │ (hot standby /  │
     │  read queries)  │
     └─────────────────┘
```

### Components

| Component | Role | HA Mechanism |
|-----------|------|-------------|
| PostgreSQL Primary | Write database | Streaming replication |
| PostgreSQL Replica | Read queries + failover standby | Hot standby |
| PgBouncer | Connection pooling | Reduces connection overhead |
| Redis Primary | Cache + task queue | Sentinel monitoring |
| Redis Replica | Failover target | Auto-promoted by Sentinel |
| Redis Sentinel ×3 | Failure detection | Quorum-based failover (2/3) |
| Orchestrator ×2 | API + task processing | Round-robin via Traefik |

## Prerequisites

- Docker Engine ≥ 24 with Compose V2
- At least 4 GB RAM available
- All base requirements from [INSTALLATION.md](INSTALLATION.md)
- Production setup (Traefik + HTTPS) recommended — see [docker-compose.prod.yml](../docker-compose.prod.yml)

## Quick Start

### 1. Set environment variables

Add to your `.env` file:

```bash
# Existing required vars
DB_PASSWORD=<strong-password>
ENCRYPTION_KEY=<fernet-key>
API_SECRET_KEY=<strong-key>
ANTHROPIC_API_KEY=<your-key>

# HA-specific
REPLICATION_PASSWORD=<strong-replication-password>   # PostgreSQL replication user
```

### 2. Start the HA stack

```bash
# Development (no HTTPS)
docker compose -f docker-compose.yml -f docker-compose.ha.yml up -d --build

# Production (with HTTPS via Traefik)
docker compose \
  -f docker-compose.yml \
  -f docker-compose.ha.yml \
  -f docker-compose.prod.yml \
  up -d --build
```

### 3. Verify replication is running

```bash
# PostgreSQL: check streaming replication status
docker exec ai-employee-postgres-primary \
  psql -U ai_employee -c "SELECT application_name, state, sync_state FROM pg_stat_replication;"

# Expected output:
#  application_name | state     | sync_state
# ------------------+-----------+-----------
#  replica1         | streaming | async

# Redis: check Sentinel sees both master and replica
docker exec ai-employee-redis-sentinel-1 \
  redis-cli -p 26379 sentinel masters

# Check replica status
docker exec ai-employee-redis-sentinel-1 \
  redis-cli -p 26379 sentinel replicas mymaster
```

## Failover Procedures

### PostgreSQL Failover (manual)

If the primary fails and you need to promote the replica:

```bash
# 1. Confirm primary is down
docker exec ai-employee-postgres-primary pg_isready || echo "Primary is down"

# 2. Promote replica to new primary
docker exec ai-employee-postgres-replica \
  touch /tmp/promote_replica
# OR use pg_promote()
docker exec ai-employee-postgres-replica \
  psql -U ai_employee -c "SELECT pg_promote();"

# 3. Update DATABASE_URL in .env to point to the replica
# DATABASE_URL=postgresql+asyncpg://ai_employee:<password>@postgres-replica:5432/ai_employee

# 4. Restart orchestrator to pick up new DB URL
docker compose restart orchestrator

# 5. Once original primary is repaired, reconfigure it as a new replica
```

### Redis Failover (automatic)

Redis Sentinel handles failover automatically when 2 of 3 sentinels detect the primary is down (after 5 seconds). No manual intervention is needed.

To manually trigger a failover for testing:

```bash
# Force sentinel to initiate failover
docker exec ai-employee-redis-sentinel-1 \
  redis-cli -p 26379 sentinel failover mymaster

# Verify new master
docker exec ai-employee-redis-sentinel-1 \
  redis-cli -p 26379 sentinel get-master-addr-by-name mymaster
```

### Orchestrator Failover (automatic)

Traefik automatically stops routing to unhealthy orchestrator replicas. Docker Compose restarts failed containers (up to 3 attempts).

To manually scale orchestrators:

```bash
# Scale up
docker compose -f docker-compose.yml -f docker-compose.ha.yml \
  up -d --scale orchestrator=3

# Scale down
docker compose -f docker-compose.yml -f docker-compose.ha.yml \
  up -d --scale orchestrator=1
```

## Monitoring HA Status

### Quick health check script

```bash
#!/bin/bash
echo "=== PostgreSQL Replication ==="
docker exec ai-employee-postgres-primary \
  psql -U ai_employee -c "SELECT application_name, state, sent_lsn, write_lsn, flush_lsn, replay_lsn FROM pg_stat_replication;" 2>/dev/null \
  || echo "PRIMARY DOWN"

echo ""
echo "=== Redis Sentinel ==="
docker exec ai-employee-redis-sentinel-1 \
  redis-cli -p 26379 sentinel masters 2>/dev/null \
  || echo "SENTINEL-1 DOWN"

echo ""
echo "=== Orchestrator Instances ==="
docker compose -f docker-compose.yml -f docker-compose.ha.yml ps orchestrator
```

### Prometheus metrics (if monitoring stack is running)

See [monitoring/](../monitoring/) for Prometheus + Grafana dashboards. Key metrics to watch:

- `pg_replication_lag` — PostgreSQL replication lag in seconds
- `redis_connected_slaves` — Number of connected Redis replicas
- `redis_sentinel_masters` — Sentinel-monitored masters
- HTTP error rates on orchestrator instances via Traefik metrics

## Connection Pooling (PgBouncer)

PgBouncer pools connections to PostgreSQL to handle many concurrent orchestrator connections efficiently.

- **Pool mode**: transaction (connection returned to pool after each transaction)
- **Max client connections**: 200
- **Pool size**: 20 connections per database/user pair

To connect directly to PgBouncer (bypassing PgBouncer for admin queries):

```bash
# Connect via PgBouncer (pooled)
docker exec -it ai-employee-pgbouncer \
  psql -h localhost -p 5432 -U ai_employee ai_employee

# Check pool statistics
docker exec ai-employee-pgbouncer \
  psql -h localhost -p 5432 -U ai_employee pgbouncer -c "SHOW POOLS;"
```

## RTO / RPO Targets

| Scenario | Recovery Time Objective | Recovery Point Objective |
|----------|------------------------|-------------------------|
| Orchestrator crash | < 30 seconds (auto-restart) | 0 (stateless) |
| Redis primary failure | < 10 seconds (Sentinel failover) | < 1 second |
| PostgreSQL primary failure (auto) | Manual promotion required | < 1 WAL segment (~16 MB) |
| PostgreSQL primary failure (with Patroni) | < 30 seconds | < 1 WAL segment |

> **Note**: For fully automatic PostgreSQL failover, consider adding [Patroni](https://github.com/patroni/patroni) or using a managed PostgreSQL service (AWS RDS Multi-AZ, Google Cloud SQL HA).

## Limitations and Known Issues

1. **PostgreSQL failover is manual**: The built-in setup uses streaming replication without automatic promotion. See the Patroni note above for fully automatic failover.
2. **Orchestrator WebSocket sessions**: Active WebSocket connections to a crashed orchestrator replica will disconnect (clients should reconnect automatically).
3. **Redis Sentinel in Docker**: Sentinels must be able to reach both master and replicas by hostname. Ensure `redis-primary` and `redis-replica` hostnames are resolvable in the `internal` network.
4. **Shared agent state**: All orchestrator replicas share state via PostgreSQL + Redis, so horizontal scaling is safe for stateless requests. Long-running agent tasks are tracked in the database.

## Upgrading the HA Stack

```bash
# Pull latest images and rebuild
docker compose -f docker-compose.yml -f docker-compose.ha.yml pull
docker compose -f docker-compose.yml -f docker-compose.ha.yml up -d --build

# Rolling restart of orchestrator replicas (zero downtime with 2+ replicas)
docker compose -f docker-compose.yml -f docker-compose.ha.yml \
  up -d --no-deps --scale orchestrator=2 orchestrator
```
