import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.api.v1.api import api_router

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

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    description="Backend API for Retail Shelf Monitoring & Restocking Assistant",
    openapi_tags=openapi_tags,
)

logger = logging.getLogger(__name__)


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


# Set all CORS enabled origins
# In production, this should be configured with specific origins from settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
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
