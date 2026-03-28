"""System info endpoint — exposes build-time metadata baked in by the engine."""

import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/system", tags=["system"])

_APP_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_ROOT = _APP_ROOT.parent
_DEPLOY_VERSION_FILE = _APP_ROOT / "_deploy_version.py"
_DEPLOY_VERSION_RE = re.compile(r"DEPLOY_VERSION(?:\s*:\s*[^=]+)?\s*=\s*(\d+)")


def _get_version() -> str:
    """Get version from pyproject.toml"""
    try:
        with (_BACKEND_ROOT / "pyproject.toml").open("rb") as f:
            data = tomllib.load(f)
            return data.get("project", {}).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


def _get_deploy_version() -> int:
    """Read the baked deploy version without importing a generated Python module."""
    try:
        content = _DEPLOY_VERSION_FILE.read_text(encoding="utf-8")
    except Exception:
        return 0

    match = _DEPLOY_VERSION_RE.search(content)
    if match is None:
        return 0

    try:
        return int(match.group(1))
    except ValueError:
        return 0


@router.get("/info")
async def system_info() -> dict:
    """Return system metadata including the autonomous deploy version.

    ``deploy_version`` is baked into the Docker image by the Self-Evolving
    Engine on every successful deploy. It starts at 0 on a fresh install and
    increments by 1 with each autonomous evolution cycle that produces
    a deployment.
    """
    version = _get_version()
    return {
        "ok": True,
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "backend",
        "deploy_version": _get_deploy_version(),
        "version": version,
    }
