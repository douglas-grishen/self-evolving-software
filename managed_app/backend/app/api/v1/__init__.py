"""API v1 routes."""

from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.api.v1.monitor import router as monitor_router

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(health_router)
v1_router.include_router(monitor_router)  # Autonomic Manager control-plane interface
