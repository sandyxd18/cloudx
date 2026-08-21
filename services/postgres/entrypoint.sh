#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# CloudX PostgreSQL entrypoint — primary or standby (streaming replication)
# ═══════════════════════════════════════════════════════════════════════════════
# Selected by POSTGRES_ROLE:
#   primary  — normal server + a replication role + pg_hba entry for standbys
#   standby  — pg_basebackup from POSTGRES_PRIMARY_HOST, then run read-only replica
#
# Env:
#   POSTGRES_ROLE                 primary | standby
#   POSTGRES_REPLICATION_USER     replication role name
#   POSTGRES_REPLICATION_PASSWORD replication role password
#   POSTGRES_PRIMARY_HOST/PORT    (standby only) where to stream from
#   PGDATA                        data dir (default /var/lib/postgresql/data)
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
ROLE="${POSTGRES_ROLE:-primary}"
REPL_USER="${POSTGRES_REPLICATION_USER:-replicator}"
REPL_PASS="${POSTGRES_REPLICATION_PASSWORD:-replicator}"

setup_primary_hooks() {
    # Runs once after the DB is initialized by the stock entrypoint.
    cat > /docker-entrypoint-initdb.d/00-replication.sh <<EOF
#!/usr/bin/env bash
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "\$POSTGRES_USER" --dbname "\$POSTGRES_DB" <<-SQL
    DO \\\$\\\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${REPL_USER}') THEN
            CREATE ROLE ${REPL_USER} WITH REPLICATION LOGIN PASSWORD '${REPL_PASS}';
        END IF;
    END
    \\\$\\\$;
SQL

# Enable streaming replication in postgresql.conf
cat >> "\$PGDATA/postgresql.conf" <<-CONF
wal_level = replica
max_wal_senders = 10
max_replication_slots = 10
hot_standby = on
wal_keep_size = 512MB
CONF

# Allow standby hosts to connect for replication
echo "host replication ${REPL_USER} 0.0.0.0/0 scram-sha-256" >> "\$PGDATA/pg_hba.conf"
EOF
    chmod +x /docker-entrypoint-initdb.d/00-replication.sh
}

init_standby() {
    if [ -s "$PGDATA/PG_VERSION" ]; then
        echo "Standby data dir already initialized; starting."
        return
    fi
    echo "Bootstrapping standby from ${POSTGRES_PRIMARY_HOST}:${POSTGRES_PRIMARY_PORT}..."
    export PGPASSWORD="$REPL_PASS"
    until pg_isready -h "$POSTGRES_PRIMARY_HOST" -p "${POSTGRES_PRIMARY_PORT:-5432}"; do
        echo "  waiting for primary to accept connections..."
        sleep 3
    done
    rm -rf "${PGDATA:?}/"*
    pg_basebackup \
        -h "$POSTGRES_PRIMARY_HOST" \
        -p "${POSTGRES_PRIMARY_PORT:-5432}" \
        -U "$REPL_USER" \
        -D "$PGDATA" \
        -Fp -Xs -P -R           # -R writes standby.signal + primary_conninfo
    chmod 0700 "$PGDATA"
    echo "Standby base backup complete."
}

case "$ROLE" in
    primary)
        echo "Starting PostgreSQL as PRIMARY."
        setup_primary_hooks
        exec docker-entrypoint.sh postgres
        ;;
    standby)
        echo "Starting PostgreSQL as STANDBY."
        init_standby
        # Standby is already initialized — run postgres directly as the postgres user.
        exec docker-entrypoint.sh postgres
        ;;
    *)
        echo "ERROR: POSTGRES_ROLE must be 'primary' or 'standby' (got '$ROLE')" >&2
        exit 1
        ;;
esac
