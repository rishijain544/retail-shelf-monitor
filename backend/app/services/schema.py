"""Apply — and report on — the Alembic migration chain from inside the app.

Why this exists at all: `backend/docker-entrypoint.sh` already runs
`alembic upgrade head`, but a managed host is free to start the container without
that script (Render applies its own Docker Command, and compose-only entrypoint
conventions do not carry over). The failure is silent and expensive:

* ``GET /api/v1/health`` answers 200 because ``SELECT 1`` needs no tables, so the
  service looks healthy and the deploy is marked live;
* every ``POST /auth/signup`` and ``POST /auth/login`` returns 500 with
  ``relation "users" does not exist`` — exactly the "Internal server error" seen on
  the deployed sign-in page.

Running the upgrade here makes the schema a precondition of serving traffic no
matter how the process was started. It is a no-op once the database is at head.
``AUTO_MIGRATE`` (on by default) decides whether it runs — it is *not* gated on
``ENVIRONMENT``, because a service created outside render.yaml has no
``ENVIRONMENT=production`` and the skipped upgrade is exactly what left every
signup and login answering 500. The test suite disables it because each test
builds its own throwaway schema; local databases built by ``scripts/seed.py``
(``create_all``, so no ``alembic_version`` row) should set ``AUTO_MIGRATE=false``
to avoid a failed first revision on every restart.
"""
from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

# app/services/schema.py -> app/services -> app -> backend/
_BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = _BACKEND_DIR / "migrations"


def alembic_config() -> Config:
    """Alembic config for an in-process upgrade.

    Deliberately built *without* a config file: ``migrations/env.py`` calls
    ``fileConfig(config.config_file_name)`` when one is set, and that call
    reconfigures logging and disables the application's own loggers. The two values
    env.py needs are supplied as main options instead.
    """
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # env.py re-reads the URL from settings; setting it here keeps offline mode and
    # any future caller consistent.
    config.set_main_option("sqlalchemy.url", settings.SQLALCHEMY_DATABASE_URI)
    return config


def head_revision() -> Optional[str]:
    """The newest revision in migrations/versions, or None for an empty chain."""
    try:
        return ScriptDirectory.from_config(alembic_config()).get_current_head()
    except Exception:  # pragma: no cover - only when migrations/ is missing
        logger.warning("Could not read the migration chain from %s", MIGRATIONS_DIR, exc_info=True)
        return None


def upgrade_to_head() -> None:
    """Apply every pending migration.

    Errors propagate on purpose: a deployment that cannot bring its schema up to date
    must fail loudly instead of serving requests that all answer 500.
    """
    head = head_revision()
    logger.info("Applying database migrations up to %s", head)
    command.upgrade(alembic_config(), "head")
    logger.info("Database schema is at %s", head)


def schema_status(db: Session) -> str:
    """A short, human-readable schema state for the health endpoint.

    Returns ``head``, ``outdated: <rev> (head <rev>)`` or
    ``not_initialised: <reason>``. Never raises — a health check that fails is worse
    than one that explains itself.
    """
    head = head_revision()
    try:
        current = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception as exc:
        # A missing alembic_version table means the chain was never applied to this
        # database (a create_all-only database looks the same), which is what the
        # operator needs to see. The rollback is best-effort: the health check may be
        # handed a session that is already unusable.
        with suppress(Exception):
            db.rollback()
        return f"not_initialised: {type(exc).__name__}"

    if not current:
        return f"not_initialised: no revision recorded (head {head})"
    if current == head:
        return "head"
    return f"outdated: {current} (head {head})"
