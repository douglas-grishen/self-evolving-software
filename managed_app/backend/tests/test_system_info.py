"""Tests for system metadata endpoint helpers."""

import pytest

from app.api.v1 import system as system_api


def test_get_deploy_version_supports_typed_assignment(monkeypatch, tmp_path):
    """Typed deploy-version assignments should still be parsed correctly."""
    version_file = tmp_path / "_deploy_version.py"
    version_file.write_text("DEPLOY_VERSION: int = 7\n", encoding="utf-8")

    monkeypatch.setattr(system_api, "_DEPLOY_VERSION_FILE", version_file)

    assert system_api._get_deploy_version() == 7


@pytest.mark.asyncio
async def test_system_info_returns_zero_when_deploy_version_file_is_invalid(
    monkeypatch, tmp_path
):
    """A malformed generated version file must not take down the endpoint."""
    version_file = tmp_path / "_deploy_version.py"
    version_file.write_text("<<<<<<< broken deploy version >>>>>>>\n", encoding="utf-8")

    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text('[project]\nversion = "9.9.9"\n', encoding="utf-8")

    monkeypatch.setattr(system_api, "_DEPLOY_VERSION_FILE", version_file)
    monkeypatch.setattr(system_api, "_BACKEND_ROOT", tmp_path)

    response = await system_api.system_info()

    assert response["ok"] is True
    assert response["status"] == "ok"
    assert response["service"] == "backend"
    assert "timestamp" in response
    assert response["deploy_version"] == 0
    assert response["version"] == "9.9.9"
