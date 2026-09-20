#!/usr/bin/env bash
# Start a throwaway PostgreSQL cluster for the test suite and create the two
# roles the row level security tests need.
#
#   ./scripts/dev_postgres.sh start
#   ./scripts/dev_postgres.sh stop
#
# In CI or on a machine with Docker, `docker compose up -d postgres` does the
# same thing with pgvector already installed.
set -euo pipefail

PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
PGDATA="${PGDATA:-/var/lib/postgresql/dev-cluster}"
PGPORT="${PGPORT:-5433}"
RUN_AS="${RUN_AS:-postgres}"

start() {
  if [ ! -d "$PGDATA/base" ]; then
    mkdir -p "$PGDATA"
    chown -R "$RUN_AS" "$PGDATA"
    chmod 700 "$PGDATA"
    su "$RUN_AS" -c "$PGBIN/initdb -D $PGDATA -U postgres --auth=trust"
  fi
  su "$RUN_AS" -c "$PGBIN/pg_ctl -D $PGDATA -o '-p $PGPORT -c listen_addresses=127.0.0.1' -w start"

  psql -h 127.0.0.1 -p "$PGPORT" -U postgres -v ON_ERROR_STOP=0 <<SQL
CREATE ROLE app_owner LOGIN PASSWORD 'app_owner' CREATEDB;
CREATE ROLE app LOGIN PASSWORD 'app' NOBYPASSRLS;
CREATE ROLE app_system LOGIN PASSWORD 'app_system' BYPASSRLS;
SQL

  for db in ai_marketing ai_marketing_test; do
    psql -h 127.0.0.1 -p "$PGPORT" -U postgres -tAc \
      "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 || \
      psql -h 127.0.0.1 -p "$PGPORT" -U postgres -c "CREATE DATABASE $db OWNER app_system"
    psql -h 127.0.0.1 -p "$PGPORT" -U postgres -d "$db" <<SQL
GRANT USAGE, CREATE ON SCHEMA public TO app_system;
GRANT USAGE ON SCHEMA public TO app;
ALTER DEFAULT PRIVILEGES FOR ROLE app_system IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app;
ALTER DEFAULT PRIVILEGES FOR ROLE app_system IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app;
SQL
  done
  echo "PostgreSQL ready on 127.0.0.1:$PGPORT"
}

stop() {
  su "$RUN_AS" -c "$PGBIN/pg_ctl -D $PGDATA -m fast -w stop"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  *) echo "usage: $0 {start|stop}" >&2; exit 2 ;;
esac
