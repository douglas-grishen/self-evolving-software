"""Engine-facing adapter for the shared runtime skills implementation."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from engine.config import EngineSettings, settings


def _candidate_backend_paths(config: EngineSettings) -> list[Path]:
    candidates = [
        Path(config.evolved_app_path) / "backend",
        Path(config.operational_plane_path) / "backend",
        Path(config.repo_root) / "managed_app" / "backend",
    ]
    result: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def ensure_backend_app_importable(config: EngineSettings | None = None) -> Path:
    """Add the managed backend package to sys.path so the engine can reuse skill code."""
    cfg = config or settings
    for backend_path in _candidate_backend_paths(cfg):
        if (backend_path / "app").exists():
            path_str = str(backend_path)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)
            return backend_path
    raise RuntimeError("Could not locate managed_app/backend to load shared skills runtime")


class SkillRegistry:
    """Thin wrapper over the shared backend skill registry."""

    def __init__(self, config: EngineSettings | None = None) -> None:
        ensure_backend_app_importable(config)
        from app.skills_runtime import SkillRegistry as SharedSkillRegistry

        self._registry = SharedSkillRegistry()

    def list_skills(self) -> list[Any]:
        return self._registry.list_skills()

    def get(self, key: str) -> Any:
        return self._registry.get(key)


class SkillExecutor:
    """Thin wrapper over the shared backend skill executor."""

    def __init__(self, config: EngineSettings | None = None) -> None:
        ensure_backend_app_importable(config)
        from app.skills_runtime import SkillExecutor as SharedSkillExecutor

        self._executor = SharedSkillExecutor()

    @property
    def registry(self) -> Any:
        return self._executor.registry

    async def invoke(
        self,
        record: Any,
        payload: dict[str, Any],
        settings_map: dict[str, str] | None = None,
    ) -> Any:
        from app.skills_runtime import SkillInvocationRequest

        return await self._executor.invoke(
            record,
            SkillInvocationRequest.model_validate(payload),
            settings_map=settings_map,
        )
