"""
backend/scripts/check_migrations_postgres.py

Apply the whole Alembic chain to a throwaway *PostgreSQL* database and drop it again.

The pytest suite runs on SQLite, which silently accepts dialect quirks that PostgreSQL
rejects: revision d5e8b3f1c7a4 wrote `low_confidence = 1` into a real boolean column and
aborted `alembic upgrade head` on a fresh PostgreSQL volume, crash-looping the backend
container while every SQLite test stayed green. This script is the check that catches
that class of mistake before deployment.

It needs a reachable PostgreSQL server, so it is a separate opt-in command rather than
part of the default pytest run (which must not require Docker).

Usage, against the compose `db` service:

    docker compose exec -T backend python scripts/check_migrations_postgres.py

or from `backend/` with a local PostgreSQL configured through `.env`:

    python scripts/check_migrations_postgres.py

Exit status is 0 only when the chain applied, every expected table exists, and
`alembic_version` matches the script head. The throwaway database is always dropped.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote_plus

# Put backend/ in sys.path so `app` and `alembic.ini` resolve from any cwd.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import psycopg2
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg2 import sql

from app.core.config import settings

# Every table the chain is expected to leave behind, derived from the models.
EXPECTED_TABLES = {
    "alembic_version",
    "alerts",
    "cameras",
    "captures",
    "detections",
    "email_settings",
    "email_verification_codes",
    "mfa_recovery_codes",
    "password_reset_tokens",
    "products",
    "shelf_reports",
    "stores",
    "users",
}


def _connect(database: str):
    return psycopg2.connect(
        host=settings.POSTGRES_SERVER,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=database,
    )


def _database_url(database: str) -> str:
    """Build a URL for alembic; credentials are quoted so odd passwords still parse."""
    return (
        f"postgresql://{quote_plus(settings.POSTGRES_USER)}:"
        f"{quote_plus(settings.POSTGRES_PASSWORD or '')}@"
        f"{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/{database}"
    )


def _script_head() -> str:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("migrations/ has no head revision")
    return head


def main() -> int:
    database = f"shelf_migration_check_{uuid.uuid4().hex[:8]}"
    target = f"{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}"

    try:
        admin = _connect("postgres")
    except psycopg2.Error as exc:
        print(f"Cannot reach PostgreSQL at {target}: {exc}")
        print(
            "This check needs a server. Against the compose stack run:\n"
            "  docker compose exec -T backend python scripts/check_migrations_postgres.py"
        )
        return 1

    admin.autocommit = True  # CREATE/DROP DATABASE cannot run in a transaction.
    created = False
    try:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        created = True
        print(f"created throwaway database {database} on {target}")

        env = {**os.environ, "DATABASE_URL": _database_url(database)}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(_BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
        )
        print((result.stdout + result.stderr).strip())
        if result.returncode != 0:
            print("FAILED: alembic upgrade head did not apply to a fresh PostgreSQL database")
            return 1

        connection = _connect(database)
        try:
            with connection, connection.cursor() as cur:
                cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                tables = {row[0] for row in cur.fetchall()}
                cur.execute("SELECT version_num FROM alembic_version")
                row = cur.fetchone()
                revision = row[0] if row else None
        finally:
            connection.close()

        missing = sorted(EXPECTED_TABLES - tables)
        if missing:
            print(f"FAILED: missing tables after the upgrade: {', '.join(missing)}")
            return 1

        head = _script_head()
        print(f"tables ({len(tables)}): {', '.join(sorted(tables))}")
        if revision != head:
            print(f"FAILED: alembic_version is {revision!r}, expected head {head!r}")
            return 1

        print(f"OK: chain applied to a fresh PostgreSQL database; alembic_version = {revision}")
        return 0
    finally:
        # Plain DROP (no WITH FORCE) keeps this working on PostgreSQL older than 13;
        # every connection above is closed first so nothing blocks the drop.
        if created:
            try:
                with admin.cursor() as cur:
                    cur.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
                print(f"dropped throwaway database {database}")
            except psycopg2.Error as exc:  # never mask the original failure
                print(f"warning: could not drop {database}: {exc}")
        admin.close()


if __name__ == "__main__":
    sys.exit(main())
