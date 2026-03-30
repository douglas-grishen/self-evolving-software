"""Tests for settings aliases during plane terminology migration."""

from pathlib import Path

from engine.config import EngineSettings


def test_settings_accept_new_operational_plane_env(monkeypatch):
    """The preferred operational-plane env var should populate the settings field."""
    monkeypatch.setenv("ENGINE_OPERATIONAL_PLANE_PATH", "/tmp/operational-plane")
    monkeypatch.delenv("ENGINE_MANAGED_APP_PATH", raising=False)

    settings = EngineSettings()

    assert settings.operational_plane_path == Path("/tmp/operational-plane")


def test_settings_accept_legacy_managed_app_env(monkeypatch):
    """The legacy managed-app env var should remain accepted during rollout."""
    monkeypatch.delenv("ENGINE_OPERATIONAL_PLANE_PATH", raising=False)
    monkeypatch.setenv("ENGINE_MANAGED_APP_PATH", "/tmp/legacy-managed-app")

    settings = EngineSettings()

    assert settings.operational_plane_path == Path("/tmp/legacy-managed-app")


def test_empty_runtime_contracts_env_is_treated_as_unset(monkeypatch):
    """Optional contracts env should not collapse to the current working directory."""
    monkeypatch.setenv("ENGINE_RUNTIME_CONTRACTS_PATH", "")

    settings = EngineSettings()

    assert settings.runtime_contracts_path is None
