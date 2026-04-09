"""Tests for engine access to the shared runtime skills contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.agents.data_manager import DataManagerAgent
from engine.config import EngineSettings
from engine.context import create_context
from engine.models.skills import AvailableSkill
from engine.skills.runtime import SkillRegistry, ensure_backend_app_importable


def test_engine_skill_registry_can_load_backend_runtime():
    """The engine should reuse the backend skill registry instead of forking its own copy."""
    repo_root = Path(__file__).resolve().parents[2]
    config = EngineSettings(
        repo_root=repo_root,
        operational_plane_path=repo_root / "managed_app",
        evolved_app_path=repo_root / "managed_app",
    )

    backend_path = ensure_backend_app_importable(config)
    registry = SkillRegistry(config)

    assert (backend_path / "app").exists()
    assert registry.get("web-browser").metadata().key == "web-browser"


@pytest.mark.asyncio
async def test_data_manager_attaches_available_skills_to_context(tmp_path):
    """Repo analysis should carry runtime skill inventory forward for downstream prompts."""
    app_root = tmp_path / "managed_app"
    (app_root / "frontend" / "src").mkdir(parents=True)
    (app_root / "backend" / "app").mkdir(parents=True)
    (app_root / "frontend" / "src" / "App.tsx").write_text(
        "export default function App() { return null; }"
    )
    (app_root / "backend" / "app" / "main.py").write_text("app = object()")

    class _Reporter:
        async def fetch_lessons(self):
            return []

        async def fetch_skills(self):
            return [
                AvailableSkill(
                    key="web-browser",
                    name="Web Browser",
                    description="Structured browser automation",
                )
            ]

    agent = DataManagerAgent(managed_app_path=app_root, event_reporter=_Reporter())

    ctx = await agent.execute(create_context("Inspect runtime skills"))

    assert ctx.available_skills[0].key == "web-browser"
