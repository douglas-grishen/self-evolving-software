"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import v1_router
from app.auth import ensure_default_admin
from app.config import settings
from app.database import async_session
from app.api.v1.skills import ensure_default_skills
from app.middleware.metrics import MetricsMiddleware
from app.system_settings import ensure_default_system_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifecycle."""
    # Seed default admin user on first startup
    async with async_session() as session:
        await ensure_default_admin(session)
        await ensure_default_system_settings(session)
        await ensure_default_skills(session)
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# --- Middleware (order matters: outermost runs first on request, last on response)

# MetricsMiddleware must wrap the full request so it captures all latencies/errors
app.add_middleware(MetricsMiddleware)

# CORS — restricts browser origins for the Managed System's public API
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routes
app.include_router(v1_router)


@app.get("/health")
async def root_health() -> dict:
    """Root-level health check (for load balancer / Docker healthcheck probes)."""
    return {"status": "ok"}
