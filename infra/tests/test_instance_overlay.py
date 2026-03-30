"""Tests for deploy configuration fallback without tracked instance overlays."""

import importlib
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "infra"))

load_instance_overlay = importlib.import_module("instance_overlay").load_instance_overlay


def test_load_instance_overlay_falls_back_to_repo_defaults_without_overlay(tmp_path):
    overlay = load_instance_overlay(tmp_path, "demo")

    assert overlay.instance_key == "demo"
    assert overlay.public_host == "demo.local"
    assert overlay.compose_project == "self-evolving-software-demo"
    assert overlay.db_name == "ses_demo"
    assert overlay.genesis_repo_path == "genesis.yaml"
    assert overlay.contracts_repo_path == "contracts.example.yaml"


def test_load_instance_overlay_uses_private_overlay_values_when_present(tmp_path):
    overlay_dir = tmp_path / "instances" / "custom"
    overlay_dir.mkdir(parents=True)
    (overlay_dir / "instance.env").write_text(
        "\n".join(
            [
                "INSTANCE_KEY=custom",
                "PUBLIC_HOST=example.test",
                "COMPOSE_PROJECT=ses-custom",
                "POSTGRES_DB=ses_custom_db",
            ]
        ),
        encoding="utf-8",
    )
    (overlay_dir / "genesis.yaml").write_text("version: '1.0.0'\n", encoding="utf-8")
    (overlay_dir / "contracts.yaml").write_text("apps: {}\n", encoding="utf-8")

    overlay = load_instance_overlay(tmp_path, "custom")

    assert overlay.public_host == "example.test"
    assert overlay.compose_project == "ses-custom"
    assert overlay.db_name == "ses_custom_db"
    assert overlay.genesis_repo_path == "instances/custom/genesis.yaml"
    assert overlay.contracts_repo_path == "instances/custom/contracts.yaml"


def test_load_instance_overlay_falls_back_per_seed_when_overlay_is_partial(tmp_path):
    overlay_dir = tmp_path / "instances" / "partial"
    overlay_dir.mkdir(parents=True)
    (overlay_dir / "instance.env").write_text("INSTANCE_KEY=partial\n", encoding="utf-8")
    (overlay_dir / "genesis.yaml").write_text("version: '1.0.0'\n", encoding="utf-8")

    overlay = load_instance_overlay(tmp_path, "partial")

    assert overlay.genesis_repo_path == "instances/partial/genesis.yaml"
    assert overlay.contracts_repo_path == "contracts.example.yaml"
