"""API v1 routes."""

import importlib
import logging
import pkgutil
from pathlib import Path

from fastapi import APIRouter

from app.api.v1.apps import router as apps_router
from app.api.v1.auth import router as auth_router
from app.api.v1.chat import router as chat_router
from app.api.v1.evolution import router as evolution_router
from app.api.v1.health import router as health_router
from app.api.v1.monitor import router as monitor_router
from app.api.v1.settings import router as settings_router
from app.api.v1.skills import router as skills_router
from app.api.v1.system import router as system_router

logger = logging.getLogger(__name__)

# ── Core framework routers (always loaded, never modified by engine) ──────────
_CORE_MODULES = {
    "auth",
    "chat",
    "health",
    "monitor",
    "evolution",
    "apps",
    "system",
    "settings",
    "skills",
    "__init__",
}
_LEGACY_IMPORT_MARKERS = (
    "from app.core",
    "import app.core",
    "from app.models.github_discovery",
    "import app.models.github_discovery",
)
_SKIPPABLE_MISSING_MODULES = {
    "app.core",
    "app.models.github_discovery",
}


def _should_skip_dynamic_router_module(module_path: Path) -> tuple[bool, str | None]:
    """Skip legacy generated routers that import known-removed framework modules."""
    try:
        content = module_path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"unreadable: {exc}"

    for marker in _LEGACY_IMPORT_MARKERS:
        if marker in content:
            return True, f"legacy import marker `{marker}`"

    return False, None

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(auth_router)       # Authentication (login, token)
v1_router.include_router(chat_router)       # System assistant chat runtime
v1_router.include_router(health_router)
v1_router.include_router(monitor_router)    # Evolution Plane control-plane interface
v1_router.include_router(evolution_router)  # Evolution tracking + Inception API
v1_router.include_router(apps_router)       # Apps, Features & Capabilities framework
v1_router.include_router(system_router)     # System metadata (deploy version, etc.)
v1_router.include_router(settings_router)   # Runtime configuration (settings)
v1_router.include_router(skills_router)     # Runtime skills registry + invocation

# ── Auto-discover engine-generated routers ────────────────────────────────────
# The engine can add new API modules to this package without modifying this file.
# Any module in app/api/v1/ that exposes a `router` variable (FastAPI APIRouter)
# and is not a core module will be automatically registered.
_v1_pkg_path = Path(__file__).parent
for _mod_info in pkgutil.iter_modules([str(_v1_pkg_path)]):
    if _mod_info.name in _CORE_MODULES:
        continue
    _module_path = _v1_pkg_path / f"{_mod_info.name}.py"
    _skip_module, _skip_reason = _should_skip_dynamic_router_module(_module_path)
    if _skip_module:
        logger.info(
            "api.v1.auto_register_skipped_legacy_module: module=%s reason=%s",
            _mod_info.name,
            _skip_reason,
        )
        continue
    try:
        _mod = importlib.import_module(f"app.api.v1.{_mod_info.name}")
        _router = getattr(_mod, "router", None)
        if _router is not None and isinstance(_router, APIRouter):
            v1_router.include_router(_router)
            logger.info("api.v1.auto_registered_router: %s", _mod_info.name)
    except ModuleNotFoundError as _exc:
        if _exc.name in _SKIPPABLE_MISSING_MODULES:
            logger.info(
                "api.v1.auto_register_skipped_missing_dependency: module=%s missing=%s",
                _mod_info.name,
                _exc.name,
            )
            continue
        logger.warning(
            "api.v1.auto_register_failed: module=%s error=%s", _mod_info.name, _exc
        )
    except Exception as _exc:
        logger.warning(
            "api.v1.auto_register_failed: module=%s error=%s", _mod_info.name, _exc
        )
