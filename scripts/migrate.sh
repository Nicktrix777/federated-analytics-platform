#!/usr/bin/env bash
# ======================================================
# Apply postgres-meta migrations in order.
#
# The init SQL (init/postgres-meta-init.sql) only runs when the
# postgres-meta-data volume is EMPTY. Existing environments pick up
# schema changes through the numbered files in scripts/migrations/,
# applied here and tracked in the schema_migrations table.
#
# Usage:
#   ./scripts/migrate.sh                 # against the fap-postgres-meta container
#   PSQL="psql -h localhost -p 5434 -U meta_user -d analytics_meta" ./scripts/migrate.sh
# ======================================================
set -euo pipefail

MIGRATIONS_DIR="$(cd "$(dirname "$0")/migrations" && pwd)"
PSQL="${PSQL:-docker exec -i fap-postgres-meta psql -U meta_user -d analytics_meta}"

run_sql() { $PSQL -v ON_ERROR_STOP=1 "$@"; }

echo "==> Ensuring schema_migrations table exists"
run_sql -q <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    VARCHAR(255) PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT NOW()
);
SQL

applied=0
skipped=0
for file in "$MIGRATIONS_DIR"/*.sql; do
    version="$(basename "$file" .sql)"
    already="$(run_sql -tAq -c "SELECT 1 FROM schema_migrations WHERE version = '$version'")"
    if [ "$already" = "1" ]; then
        echo "  - $version (already applied)"
        skipped=$((skipped + 1))
        continue
    fi
    echo "==> Applying $version"
    run_sql -q < "$file"
    run_sql -q -c "INSERT INTO schema_migrations (version) VALUES ('$version')"
    applied=$((applied + 1))
done

echo "Done: $applied applied, $skipped skipped."
