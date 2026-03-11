#!/bin/bash
# PostgreSQL Primary Node Initialization Script
# Sets up streaming replication for HA

set -e

echo "Configuring PostgreSQL primary for streaming replication..."

# Create replication user
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE USER replicator WITH REPLICATION ENCRYPTED PASSWORD '${REPLICATION_PASSWORD:-replicapassword}';
EOSQL

# Configure pg_hba.conf to allow replication from replica
cat >> "$PGDATA/pg_hba.conf" <<EOF

# Streaming replication
host    replication     replicator      postgres-replica/32     scram-sha-256
host    replication     replicator      172.0.0.0/8             scram-sha-256
EOF

# Configure postgresql.conf for replication
cat >> "$PGDATA/postgresql.conf" <<EOF

# Streaming Replication (Primary)
wal_level = replica
max_wal_senders = 3
max_replication_slots = 3
wal_keep_size = 256MB
synchronous_commit = on
EOF

echo "Primary replication setup complete."
