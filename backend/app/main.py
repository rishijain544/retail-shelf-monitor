import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import SessionLocal
from app.api.v1.api import api_router
from app.services import demo_account, email_settings, schema

# Tag descriptions surface as section intros in the Swagger UI at /docs.
openapi_tags = [
    {"name": "health", "description": "Service liveness and dependency (database) checks."},
    {
        "name": "captures",
        "description": "Upload a shelf image and fetch the report produced by the "
        "background analysis pipeline.",
    },
    {
        "name": "reports",
        "description": "Browse shelf reports (fill %, urgency, LLM summary) with "
        "filtering by store / aisle / urgency / date range, and pagination.",
    },
    {
        "name": "alerts",
        "description": "Alerts raised for high-urgency shelf reports, with their Slack "
        "delivery status.",
    },
    {
        "name": "auth",
        "description": "Sign up (creates a store and its owner in one step), log in, "
        "and read the current account. Returns JWT bearer tokens.",
    },
    {"name": "stores", "description": "Store directory."},
    {
        "name": "cameras",
        "description": "Camera directory — each camera belongs to a store and watches one aisle.",
    },
]

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Bring the schema up to date, then apply email credentials saved in the UI.

    The migration step does not duplicate the container ENTRYPOINT's
    `alembic upgrade head` out of caution: a managed host starts the process itself
    (Render applies its own Docker Command), and skipping the entrypoint fails
    *silently* — `/health` answers 200 because `SELECT 1` needs no tables, while every
    signup and login returns 500 because `users` does not exist. Upgrading here makes
    the schema a precondition of serving traffic on any host. It runs whenever
    `AUTO_MIGRATE` is on (the default) and is a no-op once the database is at head.
    It is not gated on `ENVIRONMENT`: a service created outside render.yaml has no
    `ENVIRONMENT=production`, and skipping the upgrade there is what left every
    deployed login answering 500. The test suite turns it off because each test
    builds its own throwaway schema.

    A refused migration is logged with its traceback and the process keeps running, so
    the deployment stays inspectable: `/health` then reports the precise schema state
    instead of the API crashing on a database it cannot fix.

    Email credentials live in the database because a deployed container's filesystem is
    read-only or ephemeral; without this hook the saved row would exist but alerts would
    still route through whatever the environment variables said. A database failure must
    not stop the API from booting, so it only downgrades alerting to env-var
    configuration.
    """
    if settings.AUTO_MIGRATE:
        try:
            schema.upgrade_to_head()
        except Exception:
            # Deliberately not fatal. A refused migration (a database built by
            # `create_all`, a role without DDL rights) would otherwise leave the host in
            # a restart loop with no API to inspect; this way the service stays up, the
            # traceback is in the deploy log, and /health reports the exact schema state
            # next to the database it belongs to.
            logger.exception(
                "Database migrations could not be applied; the API is starting against "
                "an out-of-date schema. Signup and login will fail until this is fixed — "
                "GET /api/v1/health reports the schema state."
            )

    if settings.seed_demo_account:
        try:
            with SessionLocal() as db:
                demo_account.ensure_demo_account(db)
        except Exception:
            logger.warning(
                "Could not create the demo account; real accounts are unaffected.",
                exc_info=True,
            )

    try:
        with SessionLocal() as db:
            email_settings.load_into_settings(db)
    except Exception:
        logger.warning(
            "Could not load saved email settings; alert email will fall back to "
            "environment variables.",
            exc_info=True,
        )
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    description="Backend API for Retail Shelf Monitoring & Restocking Assistant",
    openapi_tags=openapi_tags,
    lifespan=lifespan,
)


# NOTE: this must be registered BEFORE CORSMiddleware below. Starlette applies
# middleware in reverse registration order (last added = outermost), so CORS has
# to be added last to wrap this handler. If CORS were inner, the JSON 500 returned
# here would bypass CORSMiddleware and reach the browser with no
# Access-Control-Allow-Origin header — the fetch would fail as an opaque
# "Failed to fetch" network error instead of surfacing the real status/body.
@app.middleware("http")
async def unhandled_exception_middleware(request: Request, call_next):
    """Catch unhandled exceptions and return consistent JSON 500 responses."""
    try:
        return await call_next(request)
    except Exception as exc:
        logger.exception("Unhandled server exception processing %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )


# CORS: the browser calls this API directly from the Vercel-hosted frontend, so the
# deployed frontend origin must be allowed. "*" stays the default for local work, but
# with credentials enabled it lets ANY site drive the API as the signed-in user, so a
# production deployment sets CORS_ORIGINS (and optionally CORS_ORIGIN_REGEX for the
# per-branch Vercel preview URLs).
if settings.IS_STRICT_ENVIRONMENT and settings.cors_origins == ["*"]:
    logger.warning(
        "CORS_ORIGINS is '*' with ENVIRONMENT=%s: any website can call this API with "
        "the visitor's credentials. Set CORS_ORIGINS to the deployed frontend origin.",
        settings.ENVIRONMENT,
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include API Router
app.include_router(api_router, prefix=settings.API_V1_STR)

# Serve uploaded capture images at /uploads (Capture.image_url points here)
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

@app.get("/")
def root():
    """
    Root endpoint redirect or information message.
    """
    return {
        "message": f"Welcome to {settings.PROJECT_NAME} API",
        "docs_url": "/docs",
        "health_check_url": f"{settings.API_V1_STR}/health"
    }
