# Disaster Recovery Test Results

This file records the results of periodic DR (Disaster Recovery) test runs.
Tests are executed by `scripts/test_disaster_recovery.sh`.

---

## RTO / RPO Targets

| Objective | Target | Measurement Method |
|-----------|--------|--------------------|
| **RTO** (Recovery Time Objective) | < 30 minutes | Time from failure detection to service restoration |
| **RPO** (Recovery Point Objective) | < 24 hours | Maximum data loss (daily backup frequency) |

---

## DR Test Procedures

### Test Scenarios

| Scenario | Description | Frequency |
|----------|-------------|-----------|
| `db` | PostgreSQL failure + restore | Monthly |
| `service` | Orchestrator crash + auto-recovery | Monthly |
| `volume` | Backup integrity verification | Weekly |
| `all` | All scenarios | Quarterly |

### Running DR Tests

```bash
# Dry run first (safe - no changes made)
./scripts/test_disaster_recovery.sh --dry-run --scenario all

# Test backup integrity only (non-destructive)
./scripts/test_disaster_recovery.sh --scenario volume

# Full DR test (requires test environment)
./scripts/test_disaster_recovery.sh --scenario all
```

### Manual Full Restore Procedure

For a complete system restore from backup:

1. **Stop application services** (preserve database container):
   ```bash
   docker compose stop orchestrator frontend agent
   ```

2. **Run restore script**:
   ```bash
   ./scripts/restore.sh --backup /var/backups/ai-employee/daily/YYYYMMDD_HHMMSS
   ```

3. **Restart all services**:
   ```bash
   docker compose up -d
   ```

4. **Verify restoration**:
   ```bash
   curl http://localhost:8000/health
   curl http://localhost:8000/api/v1/agents
   ```

5. **Check database integrity**:
   ```bash
   docker compose exec postgres psql -U postgres -d ai_employee -c "\dt"
   ```

### Estimated RTO by Scenario

| Failure Type | Expected RTO | Notes |
|-------------|--------------|-------|
| Orchestrator crash | ~2 min | Docker restart policy |
| Database restart | ~5 min | No data loss if no restore needed |
| Database restore from backup | ~15-25 min | Depends on database size |
| Full system restore | ~25-30 min | All volumes + database |
| Host machine failure | ~45 min | Requires new host + volume restore |

---

## Test Run History

*DR test results will be appended below automatically by the test script.*

<!-- Test results appended by test_disaster_recovery.sh -->
