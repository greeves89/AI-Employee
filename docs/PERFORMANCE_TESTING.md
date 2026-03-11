# Performance Testing Guide

This document describes how to run performance/load tests against the AI Employee Platform to validate SLA targets and identify bottlenecks.

---

## Table of Contents

1. [SLA Targets](#1-sla-targets)
2. [Prerequisites](#2-prerequisites)
3. [Running Tests with Locust](#3-running-tests-with-locust)
4. [Running Tests with k6](#4-running-tests-with-k6)
5. [Interpreting Results](#5-interpreting-results)
6. [Bottleneck Analysis](#6-bottleneck-analysis)
7. [Tuning Recommendations](#7-tuning-recommendations)

---

## 1. SLA Targets

| Metric | Target | Notes |
|--------|--------|-------|
| P95 response time (API) | < 500ms | For list/status endpoints |
| P99 response time (API) | < 2000ms | Overall ceiling |
| Health endpoint P95 | < 100ms | Lightweight check |
| Login endpoint P95 | < 1000ms | Auth overhead expected |
| Error rate | < 1% | 5xx errors under load |
| Sustained throughput | 100 RPS | At 100 concurrent users |

---

## 2. Prerequisites

### Install Locust (Python)
```bash
pip install locust
```

### Install k6 (Go-based, recommended for CI)
```bash
# macOS
brew install k6

# Linux (Debian/Ubuntu)
sudo gpg -k
sudo gpg --no-default-keyring \
  --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
  --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install k6

# Docker
docker run --rm -i grafana/k6 run -
```

### Start the Platform
```bash
cd /path/to/ai-employee
docker-compose up -d
# Wait for health checks to pass
docker-compose ps
```

---

## 3. Running Tests with Locust

### Interactive Mode (Web UI)
```bash
cd scripts/
locust -f load_test.py --host=http://localhost:8000
# Open http://localhost:8089
# Set: 100 users, 10 spawn rate
# Click "Start swarming"
```

### Headless Mode (100 users, 5 minutes)
```bash
locust -f scripts/load_test.py \
  --host=http://localhost:8000 \
  --users=100 \
  --spawn-rate=10 \
  --run-time=5m \
  --headless \
  --csv=results/locust_results
```

### Configure Credentials
```bash
# Edit load_test.py or set environment variables
export ADMIN_EMAIL="admin@example.com"
export ADMIN_PASSWORD="your-password"
```

---

## 4. Running Tests with k6

### Basic Run (100 users, 5 minutes)
```bash
k6 run \
  --vus 100 \
  --duration 5m \
  -e BASE_URL=http://localhost:8000 \
  -e ADMIN_EMAIL=admin@example.com \
  -e ADMIN_PASSWORD=yourpassword \
  scripts/load_test_k6.js
```

### Run with Staged Ramp-Up (Recommended)
```bash
k6 run \
  -e BASE_URL=http://localhost:8000 \
  -e ADMIN_EMAIL=admin@example.com \
  -e ADMIN_PASSWORD=yourpassword \
  scripts/load_test_k6.js
```
This uses the built-in stages: 30s ramp to 10 → 1m to 50 → 1m to 100 → 2m sustain → 30s ramp down.

### Export Results to JSON
```bash
k6 run \
  --out json=results/k6_results.json \
  -e BASE_URL=http://localhost:8000 \
  scripts/load_test_k6.js
```

### Export to Prometheus/Grafana (Production Monitoring)
```bash
k6 run \
  --out prometheus=remote_write_url=http://localhost:9090/api/v1/write \
  scripts/load_test_k6.js
```

---

## 5. Interpreting Results

### Locust Output
```
Name          Reqs  Fails  Avg(ms)  Min  Max  P90  P95  P99  RPS
GET /agents   5000    5     45      10  500   80  120  250  16.7
POST /login   1000   10    380      80 2000  650  800  1200  3.3
```

### k6 Output
```
✓ http_req_duration{name:agent_list}....: p(95)=120ms (PASS, target <500ms)
✗ http_req_duration{name:login}.........: p(95)=1200ms (FAIL, target <1000ms)
✓ http_req_failed........................: 0.5% (PASS, target <1%)
```

### Key Metrics

| Metric | Good | Warning | Critical |
|--------|------|---------|----------|
| P95 latency | < 500ms | 500-1000ms | > 1000ms |
| P99 latency | < 1000ms | 1-2000ms | > 2000ms |
| Error rate | < 0.1% | 0.1-1% | > 1% |
| RPS (100 VU) | > 50 | 20-50 | < 20 |

---

## 6. Bottleneck Analysis

### Database Connection Pool
Monitor during load test:
```bash
# Check active connections
docker exec ai-employee-postgres psql -U postgres -c \
  "SELECT count(*) FROM pg_stat_activity WHERE state='active';"

# Check pool stats (via orchestrator metrics)
curl http://localhost:8000/metrics | grep db_pool
```

**Symptom**: High latency on database queries, connection timeouts.
**Fix**: Increase `DATABASE_POOL_SIZE` in `orchestrator/app/config.py` (default: 10, try 20-30).

### Redis Connection Pool
```bash
# Check Redis info
docker exec ai-employee-redis redis-cli info stats | grep -E "(connected|commands)"
```

**Symptom**: Queue operations slow, high memory usage.
**Fix**: Increase `REDIS_MAX_CONNECTIONS` or scale Redis.

### Worker/Agent Concurrency
```bash
# Check Celery/worker queue depth
docker logs ai-employee-orchestrator 2>&1 | grep -i queue
```

### Application Server (FastAPI)
FastAPI/uvicorn defaults to CPU cores. For I/O-bound workloads:
```bash
# docker-compose.yml - increase uvicorn workers
command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

---

## 7. Tuning Recommendations

### Database Optimizations
```python
# orchestrator/app/db/session.py
engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,          # Default: 5, increase for high load
    max_overflow=30,       # Default: 10, connections beyond pool_size
    pool_timeout=30,       # Default: 30s, wait time for connection
    pool_recycle=1800,     # Recycle connections every 30min
    pool_pre_ping=True,    # Test connections before use
)
```

### Redis Optimizations
```python
# orchestrator/app/services/redis_service.py
redis = aioredis.from_url(
    REDIS_URL,
    max_connections=50,    # Increase for high concurrency
    decode_responses=True,
)
```

### Response Caching
Add caching for expensive list endpoints:
```python
from functools import lru_cache
import asyncio

# Cache agent list for 5 seconds to reduce DB load
@router.get("/agents")
@cache(ttl=5)  # Use fastapi-cache2 or similar
async def list_agents(db: AsyncSession = Depends(get_db)):
    ...
```

### Horizontal Scaling
For sustained 100+ user load, consider:
1. Run multiple orchestrator instances behind nginx load balancer
2. Use PostgreSQL connection pooler (PgBouncer)
3. Scale Redis with Sentinel or Cluster mode
4. Add CDN for frontend static assets

### Production Checklist for Performance
- [ ] Database pool size tuned (target: 20-30 connections)
- [ ] Redis max connections set (target: 50+)
- [ ] Uvicorn workers = 2 × CPU cores
- [ ] Gzip compression enabled in nginx
- [ ] Static assets served by nginx (not FastAPI)
- [ ] Database indexes created for common queries
- [ ] Connection pool warmup on startup
- [ ] APM monitoring enabled (e.g., Datadog, New Relic)

---

## Results Storage

Store test results for comparison:
```
scripts/
  load_test.py              # Locust script
  load_test_k6.js           # k6 script
results/
  YYYY-MM-DD_baseline.json  # k6 JSON output
  YYYY-MM-DD_baseline.html  # Generated HTML report
```

Compare before/after optimization changes to track improvements.
