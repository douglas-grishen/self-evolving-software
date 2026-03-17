"""System info endpoint — exposes build-time metadata baked in by the engine."""

from fastapi import APIRouter

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/info")
async def system_info() -> dict:
    """Return system metadata including the autonomous deploy version.

    ``deploy_version`` is baked into the Docker image by the Self-Evolving
    Engine on every successful deploy. It starts at 0 on a fresh install and
    increments by 1 with each autonomous evolution cycle that produces
    a deployment.
    """
    try:
        from app._deploy_version import DEPLOY_VERSION
    except ImportError:
        DEPLOY_VERSION = 0

    return {"deploy_version": DEPLOY_VERSION}
