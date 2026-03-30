"""Smoke tests for deploy shell helpers when no tracked instance overlay exists."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_common_script(repo_root: Path, extra_env: dict[str, str]) -> dict[str, str]:
    command = """
source deploy/scripts/common.sh
load_instance_environment
printf 'GENESIS=%s\n' "$GENESIS_SEED_PATH"
printf 'CONTRACTS=%s\n' "$RUNTIME_CONTRACTS_SEED_PATH"
printf 'OVERLAY=%s\n' "$INSTANCE_OVERLAY_PATH"
printf 'PUBLIC_HOST=%s\n' "$PUBLIC_HOST"
printf 'COMPOSE_PROJECT=%s\n' "$COMPOSE_PROJECT"
"""
    env = os.environ.copy()
    env.update(extra_env)
    result = subprocess.run(
        ["bash", "-lc", command],
        cwd=repo_root,
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    values: dict[str, str] = {}
    for line in result.stdout.strip().splitlines():
        key, value = line.split("=", 1)
        values[key] = value
    return values


def test_common_script_defaults_to_tracked_root_seeds_without_overlay(tmp_path):
    framework_root = tmp_path / "framework"
    framework_root.mkdir()

    values = _run_common_script(
        REPO_ROOT,
        {
            "INSTANCE_KEY": "demo",
            "FRAMEWORK_ROOT": str(framework_root),
        },
    )

    assert values["GENESIS"] == f"{framework_root}/genesis.yaml"
    assert values["CONTRACTS"] == f"{framework_root}/contracts.example.yaml"
    assert values["OVERLAY"] == "instances/demo"


def test_common_script_sources_private_overlay_env_when_present(tmp_path):
    bundle_root = tmp_path / "bundle"
    overlay_dir = bundle_root / "instances" / "demo"
    overlay_dir.mkdir(parents=True)
    (overlay_dir / "instance.env").write_text(
        "\n".join(
            [
                "PUBLIC_HOST=private.example.test",
                "COMPOSE_PROJECT=ses-demo",
            ]
        ),
        encoding="utf-8",
    )

    values = _run_common_script(
        REPO_ROOT,
        {
            "INSTANCE_KEY": "demo",
            "BUNDLE_ROOT": str(bundle_root),
            "FRAMEWORK_ROOT": str(tmp_path / "framework"),
        },
    )

    assert values["PUBLIC_HOST"] == "private.example.test"
    assert values["COMPOSE_PROJECT"] == "ses-demo"


def test_install_script_syncs_framework_alembic_tree():
    install_script = (REPO_ROOT / "deploy" / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert 'rm -rf "$EVOLVED_APP_ROOT/backend/alembic"' in install_script
    assert '"$FRAMEWORK_ROOT/managed_app/backend/alembic"' in install_script
    assert '"$FRAMEWORK_ROOT/managed_app/backend/alembic.ini"' in install_script
