"""API v1 routes."""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.health import router as health_router
from app.api.v1.monitor import router as monitor_router
from app.api.v1.evolution import router as evolution_router
from app.api.v1.apps import router as apps_router
from app.api.v1.system import router as system_router

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(auth_router)  # Authentication (login, token)
v1_router.include_router(health_router)
v1_router.include_router(monitor_router)  # Autonomic Manager control-plane interface
v1_router.include_router(evolution_router)  # Evolution tracking + Inception API
v1_router.include_router(apps_router)  # Apps, Features & Capabilities framework
v1_router.include_router(system_router)  # System metadata (deploy version, etc.)
