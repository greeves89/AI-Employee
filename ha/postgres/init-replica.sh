#!/bin/bash
# PostgreSQL Replica Node Initialization Script
# Performs base backup from primary and configures standby

set -e

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
PRIMARY_HOST="${PRIMARY_HOST:-postgres-primary}"
PRIMARY_PORT="${PRIMARY_PORT:-5432}"
REPLICATION_USER="${REPLICATION_USER:-replicator}"
REPLICATION_PASSWORD="${REPLICATION_PASSWORD:-replicapassword}"

echo "Waiting for primary to be ready..."
until pg_isready -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$REPLICATION_USER"; do
    echo "Primary not ready, waiting 2s..."
    sleep 2
done

echo "Primary is ready. Performing base backup..."

# Clear data directory (may have been initialized by postgres entrypoint)
rm -rf "$PGDATA"/*

# Perform base backup from primary
PGPASSWORD="$REPLICATION_PASSWORD" pg_basebackup \
    -h "$PRIMARY_HOST" \
    -p "$PRIMARY_PORT" \
    -U "$REPLICATION_USER" \
    -D "$PGDATA" \
    -Fp -Xs -R -P

echo "Base backup complete. Configuring standby..."

# Write recovery configuration (PostgreSQL 12+ uses standby.signal + postgresql.conf)
touch "$PGDATA/standby.signal"

cat >> "$PGDATA/postgresql.conf" <<EOF

# Standby configuration
primary_conninfo = 'host=${PRIMARY_HOST} port=${PRIMARY_PORT} user=${REPLICATION_USER} password=${REPLICATION_PASSWORD} application_name=replica1'
promote_trigger_file = '/tmp/promote_replica'
hot_standby = on
hot_standby_feedback = on
EOF

echo "Replica setup complete. Starting in standby mode."
