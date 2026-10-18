from fastapi import APIRouter
from app.api.v1.endpoints import health, captures, reports, alerts, stores, cameras, auth

api_router = APIRouter()

# Health / readiness
api_router.include_router(health.router, tags=["health"])

# Authentication (signup / login / current user)
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])

# Image upload + per-capture analysis reports
api_router.include_router(captures.router, prefix="/captures", tags=["captures"])

# Read-only resources
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
api_router.include_router(stores.router, prefix="/stores", tags=["stores"])
api_router.include_router(cameras.router, prefix="/cameras", tags=["cameras"])
