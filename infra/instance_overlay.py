"""Shared deploy configuration loader with optional private instance overlays."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_DEFAULT_FRAMEWORK_ROOT = "/opt/self-evolving-software"
_DEFAULT_EVOLVED_APP_ROOT = "/opt/evolved-app"
_DEFAULT_BUNDLE_ROOT = "/opt/self-evolving-software-release"
_DEFAULT_GENESIS_SEED = "genesis.yaml"
_DEFAULT_CONTRACTS_SEED = "contracts.example.yaml"


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _strip_wrapping_quotes(value.strip())
    return values


@dataclass(frozen=True)
class InstanceOverlay:
    """Resolved deploy configuration, optionally enriched by a private overlay."""

    instance_key: str
    instance_overlay_path: str
    public_host: str
    framework_root: str
    evolved_app_root: str
    instance_state_root: str
    compose_project: str
    db_name: str
    app_name: str
    codedeploy_app_name: str
    deployment_group_name: str
    pipeline_name: str
    genesis_repo_path: str
    contracts_repo_path: str
    seed_operational_plane_repo_path: str
    purpose_path: str
    genesis_path: str
    purpose_history_path: str
    usage_state_path: str
    runtime_contracts_path: str
    bundle_root: str


def load_instance_overlay(repo_root: Path, instance_key: str) -> InstanceOverlay:
    """Load deploy configuration, using a private overlay only when present."""
    overlay_dir = repo_root / "instances" / instance_key
    env_path = overlay_dir / "instance.env"
    values = _read_env_file(env_path) if env_path.exists() else {}
    overlay_rel = values.get("INSTANCE_OVERLAY_PATH", f"instances/{instance_key}")
    framework_root = values.get("FRAMEWORK_ROOT", _DEFAULT_FRAMEWORK_ROOT)
    evolved_app_root = values.get("EVOLVED_APP_ROOT", _DEFAULT_EVOLVED_APP_ROOT)
    instance_state_root = values.get(
        "INSTANCE_STATE_ROOT",
        f"{evolved_app_root}/.instance-state",
    )
    overlay_repo_dir = repo_root / overlay_rel

    genesis_repo_path = (
        f"{overlay_rel}/genesis.yaml"
        if (overlay_repo_dir / "genesis.yaml").exists()
        else _DEFAULT_GENESIS_SEED
    )
    contracts_repo_path = (
        f"{overlay_rel}/contracts.yaml"
        if (overlay_repo_dir / "contracts.yaml").exists()
        else _DEFAULT_CONTRACTS_SEED
    )

    return InstanceOverlay(
        instance_key=values.get("INSTANCE_KEY", instance_key),
        instance_overlay_path=overlay_rel,
        public_host=values.get("PUBLIC_HOST", f"{instance_key}.local"),
        framework_root=framework_root,
        evolved_app_root=evolved_app_root,
        instance_state_root=instance_state_root,
        compose_project=values.get(
            "COMPOSE_PROJECT",
            f"self-evolving-software-{instance_key}",
        ),
        db_name=values.get("POSTGRES_DB", f"ses_{instance_key.replace('-', '_')}"),
        app_name=values.get("APP_APP_NAME", "Managed App"),
        codedeploy_app_name=values.get(
            "CODEDEPLOY_APP_NAME",
            f"self-evolving-software-{instance_key}",
        ),
        deployment_group_name=values.get(
            "DEPLOYMENT_GROUP_NAME",
            f"self-evolving-software-{instance_key}-dg",
        ),
        pipeline_name=values.get(
            "PIPELINE_NAME",
            f"self-evolving-software-{instance_key}-pipeline",
        ),
        genesis_repo_path=genesis_repo_path,
        contracts_repo_path=contracts_repo_path,
        seed_operational_plane_repo_path=f"{overlay_rel}/seed/operational-plane",
        purpose_path=f"{instance_state_root}/purpose.yaml",
        genesis_path=f"{instance_state_root}/genesis.yaml",
        purpose_history_path=f"{instance_state_root}/purpose_history",
        usage_state_path=f"{instance_state_root}/usage.json",
        runtime_contracts_path=f"{instance_state_root}/contracts.yaml",
        bundle_root=_DEFAULT_BUNDLE_ROOT,
    )
