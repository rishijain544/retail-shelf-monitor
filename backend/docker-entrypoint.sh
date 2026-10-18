#!/bin/sh
# Container entrypoint: block until Postgres really answers, apply the migration
# chain, then hand off to the container's CMD.
#
# Works with both connection styles the project supports:
#   * docker compose — POSTGRES_SERVER/USER/PASSWORD/DB on the compose network;
#   * managed Postgres (Neon on Render) — a single DATABASE_URL, usually with
#     ?sslmode=require. Preferring DATABASE_URL here matters because alembic and the
#     app already read it; waiting on the compose defaults instead would time out
#     against a host that does not exist.
set -e

# Never print the DSN: it carries the database password.
if [ -n "$DATABASE_URL" ]; then
  echo "[entrypoint] waiting for postgres at managed DATABASE_URL"
else
  echo "[entrypoint] waiting for postgres at ${POSTGRES_SERVER:-db}:${POSTGRES_PORT:-5432}"
fi

# Postgres briefly accepts TCP connections before it will serve queries, and
# compose's `condition: service_healthy` covers the container, not this process.
# Opening a real connection is the only reliable gate for `alembic upgrade head`.
# Managed databases also scale to zero (Neon), so the first attempt often wakes them.
python - <<'PY'
import os
import sys
import time

import psycopg2

dsn = os.environ.get("DATABASE_URL")

params = {
    "host": os.environ.get("POSTGRES_SERVER", "db"),
    "port": int(os.environ.get("POSTGRES_PORT", "5432")),
    "user": os.environ.get("POSTGRES_USER", "postgres"),
    "password": os.environ.get("POSTGRES_PASSWORD", "postgres"),
    "dbname": os.environ.get("POSTGRES_DB", "shelf_monitoring"),
}

def connect():
    if dsn:
        # psycopg2 parses the URL, including sslmode=require.
        return psycopg2.connect(dsn)
    return psycopg2.connect(**params)

deadline = time.time() + 60
while True:
    try:
        connect().close()
        break
    except psycopg2.OperationalError as exc:
        if time.time() >= deadline:
            print(f"[entrypoint] postgres unreachable after 60s: {exc}", file=sys.stderr)
            sys.exit(1)
        time.sleep(1)
PY

# StaticFiles raises at import time if its directory is missing, which would
# take the whole app down on a fresh checkout or volume. On Render the persistent
# disk is mounted at /var/data and UPLOAD_DIR points inside it.
UPLOAD_DIR="${UPLOAD_DIR:-/app/uploads}"
mkdir -p "$UPLOAD_DIR"

# alembic.ini uses a relative script_location (`migrations`), so this has to run
# from /app. `set -e` means a failed migration stops the container instead of
# letting the app start against a stale schema.
echo "[entrypoint] alembic upgrade head"
cd /app
alembic upgrade head

echo "[entrypoint] starting: $*"
exec "$@"
