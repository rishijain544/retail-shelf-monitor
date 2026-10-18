#!/bin/sh
# Container entrypoint: block until Postgres really answers, apply the migration
# chain, then hand off to the container's CMD.
set -e

DB_HOST="${POSTGRES_SERVER:-db}"
DB_PORT="${POSTGRES_PORT:-5432}"

echo "[entrypoint] waiting for postgres at ${DB_HOST}:${DB_PORT}"

# Postgres briefly accepts TCP connections before it will serve queries, and
# compose's `condition: service_healthy` covers the container, not this process.
# Opening a real connection is the only reliable gate for `alembic upgrade head`.
python - <<'PY'
import os
import sys
import time

import psycopg2

params = {
    "host": os.environ.get("POSTGRES_SERVER", "db"),
    "port": int(os.environ.get("POSTGRES_PORT", "5432")),
    "user": os.environ.get("POSTGRES_USER", "postgres"),
    "password": os.environ.get("POSTGRES_PASSWORD", "postgres"),
    "dbname": os.environ.get("POSTGRES_DB", "shelf_monitoring"),
}

deadline = time.time() + 60
while True:
    try:
        psycopg2.connect(**params).close()
        break
    except psycopg2.OperationalError as exc:
        if time.time() >= deadline:
            print(f"[entrypoint] postgres unreachable after 60s: {exc}", file=sys.stderr)
            sys.exit(1)
        time.sleep(1)
PY

# StaticFiles raises at import time if its directory is missing, which would
# take the whole app down on a fresh checkout or volume.
mkdir -p /app/uploads

# alembic.ini uses a relative script_location (`migrations`), so this has to run
# from /app. `set -e` means a failed migration stops the container instead of
# letting the app start against a stale schema.
echo "[entrypoint] alembic upgrade head"
cd /app
alembic upgrade head

echo "[entrypoint] starting: $*"
exec "$@"
