"""System info endpoint — exposes build-time metadata baked in by the engine."""

from fastapi import APIRouter
import tomllib

router = APIRouter(prefix="/system", tags=["system"])


def _get_version() -> str:
    """Get version from pyproject.toml"""
    try:
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
            return data.get("project", {}).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


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

    version = _get_version()
    return {"deploy_version": DEPLOY_VERSION, "version": version}
