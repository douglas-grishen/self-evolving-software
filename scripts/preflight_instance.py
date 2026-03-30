#!/usr/bin/env python3
"""Validate deploy inputs before creating a new instance."""

from __future__ import annotations

import argparse
import importlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "infra"))
sys.path.insert(0, str(REPO_ROOT / "evolving_engine"))

Genesis = importlib.import_module("engine.models.genesis").Genesis
FrameworkInvariants = importlib.import_module(
    "engine.models.framework_invariants"
).FrameworkInvariants
load_instance_overlay = importlib.import_module("instance_overlay").load_instance_overlay

_INSTANCE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_CODECONNECTIONS_ARN_PREFIX = "arn:aws:codeconnections:"
_OFFICIAL_GITHUB_OWNER = "douglas-grishen"
_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    message: str


@dataclass(frozen=True)
class PreflightResult:
    findings: tuple[Finding, ...]

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.level == "error")

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.level == "warning")


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def _merge_env(repo_root: Path, env: dict[str, str] | None = None) -> dict[str, str]:
    merged = _read_env_file(repo_root / "infra" / "deploy.env")
    merged.update({key: value for key, value in os.environ.items() if value is not None})
    if env:
        merged.update(env)
    return merged


def _warning(code: str, message: str) -> Finding:
    return Finding(level="warning", code=code, message=message)


def _error(code: str, message: str) -> Finding:
    return Finding(level="error", code=code, message=message)


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _git_output(repo_root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip()


def _normalize_github_slug(remote_url: str) -> str | None:
    cleaned = remote_url.strip()
    if cleaned.startswith("git@github.com:"):
        slug = cleaned.split(":", 1)[1]
    else:
        match = re.search(r"github\.com[:/](.+)$", cleaned)
        if not match:
            return None
        slug = match.group(1)
    if slug.endswith(".git"):
        slug = slug[:-4]
    return slug or None


def _validate_repo_checkout(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    appspec_text = _safe_read_text(repo_root / "appspec.yml")
    docker_compose_text = _safe_read_text(repo_root / "docker-compose.prod.yml")
    engine_config_text = _safe_read_text(repo_root / "evolving_engine" / "engine" / "config.py")
    ec2_stack_text = _safe_read_text(repo_root / "infra" / "stacks" / "ec2_stack.py")

    if (repo_root / "purpose.yaml").exists():
        findings.append(
            _error(
                "legacy_purpose_seed_present",
                "Tracked purpose.yaml is still present in the deploy source. New instances must start without a business Purpose.",
            )
        )

    if "ENGINE_PURPOSE_SEED_PATH" in docker_compose_text:
        findings.append(
            _error(
                "legacy_purpose_seed_env",
                "docker-compose.prod.yml still exports ENGINE_PURPOSE_SEED_PATH. Remove the legacy first-boot Purpose seed before deploying.",
            )
        )

    if "purpose_seed_path" in engine_config_text:
        findings.append(
            _error(
                "legacy_purpose_seed_config",
                "The engine config still supports purpose_seed_path. New instances must not seed Purpose from the repository.",
            )
        )

    if "destination: /opt/self-evolving-software\n" in appspec_text:
        findings.append(
            _error(
                "legacy_appspec_destination",
                "appspec.yml still deploys the bundle directly into the framework root. CodeDeploy must stage into /opt/self-evolving-software-release so the install hook can promote the selected source safely.",
            )
        )

    if 'git clone --branch' in ec2_stack_text:
        findings.append(
            _error(
                "bootstrap_clones_github_source",
                "EC2 user-data still clones the GitHub source before CodeDeploy. New instances must wait for the pipeline artifact instead of bootstrapping from an uncontrolled checkout.",
            )
        )

    if 'docker compose -p "$COMPOSE_PROJECT" -f "$FRAMEWORK_ROOT/docker-compose.prod.yml" up -d --build' in ec2_stack_text:
        findings.append(
            _error(
                "bootstrap_starts_services_early",
                "EC2 user-data still starts services before CodeDeploy delivers the selected source bundle. This can contaminate a new instance with stale framework state.",
            )
        )

    return findings


def _validate_source_alignment(repo_root: Path, env: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    if _truthy(env.get("SKIP_GIT_SOURCE_CHECKS")):
        return findings

    status_output = _git_output(repo_root, "status", "--short")
    if status_output:
        findings.append(
            _error(
                "deploy_source_dirty",
                "The local checkout has uncommitted changes. CodePipeline deploys the configured GitHub source, not this dirty workspace. Commit/push first or set SKIP_GIT_SOURCE_CHECKS=1 intentionally.",
            )
        )

    configured_slug = f"{env.get('GITHUB_OWNER', _OFFICIAL_GITHUB_OWNER)}/{env.get('GITHUB_REPO', 'self-evolving-software')}"
    local_origin = _git_output(repo_root, "remote", "get-url", "origin")
    local_origin_slug = _normalize_github_slug(local_origin or "")
    if local_origin_slug is None:
        findings.append(
            _warning(
                "deploy_source_unknown_origin",
                "Could not determine the local git origin. Preflight cannot verify that your GitHub deploy source matches this checkout.",
            )
        )
        return findings

    if local_origin_slug != configured_slug:
        findings.append(
            _warning(
                "deploy_source_repo_differs",
                f"Configured deploy source {configured_slug!r} differs from local origin {local_origin_slug!r}. Preflight cannot prove that your local fixes exist in the remote source branch.",
            )
        )
        return findings

    configured_branch = env.get("GITHUB_BRANCH", "main")
    current_branch = _git_output(repo_root, "branch", "--show-current")
    if current_branch and current_branch != configured_branch:
        findings.append(
            _error(
                "deploy_source_branch_mismatch",
                f"Configured deploy source is branch {configured_branch!r}, but this checkout is on {current_branch!r}. CDK will deploy GitHub branch {configured_branch!r}, not the code currently checked out here.",
            )
        )

    local_head = _git_output(repo_root, "rev-parse", "HEAD")
    remote_head_raw = _git_output(repo_root, "ls-remote", "origin", f"refs/heads/{configured_branch}")
    if local_head and remote_head_raw:
        remote_head = remote_head_raw.split()[0]
        if remote_head != local_head:
            findings.append(
                _error(
                    "deploy_source_remote_drift",
                    f"Configured GitHub branch {configured_branch!r} resolves to {remote_head[:12]}, but this checkout is at {local_head[:12]}. Push the branch or change GITHUB_BRANCH before deploying.",
                )
            )
    elif local_head and current_branch == configured_branch:
        findings.append(
            _warning(
                "deploy_source_remote_unverified",
                f"Could not verify the remote HEAD for branch {configured_branch!r}. Preflight only checked the local checkout.",
            )
        )

    return findings


def _validate_contracts(path: Path) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    if not path.exists():
        return [_error("contracts_missing", f"Contracts seed file not found: {path}")], 0

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover - defensive, surfaced in tests via invalid YAML
        return [_error("contracts_invalid_yaml", f"Contracts YAML is invalid: {exc}")], 0

    if not isinstance(data, dict):
        return [_error("contracts_invalid_shape", "Contracts file must contain a YAML object.")], 0

    apps = data.get("apps", {})
    if not isinstance(apps, dict):
        return [_error("contracts_invalid_apps", "Contracts file field 'apps' must be an object.")], 0

    app_count = len(apps)
    if app_count == 0:
        findings.append(
            _warning(
                "empty_runtime_contracts",
                "No product-specific runtime contracts are defined. The framework will protect its own core routes, but not your app-specific behavior.",
            )
        )
    return findings, app_count


def run_preflight(repo_root: Path, env: dict[str, str] | None = None) -> PreflightResult:
    effective_env = _merge_env(repo_root, env)
    findings: list[Finding] = []

    findings.extend(_validate_repo_checkout(repo_root))
    findings.extend(_validate_source_alignment(repo_root, effective_env))

    instance_key = (effective_env.get("INSTANCE_KEY") or "base").strip()
    if not _INSTANCE_KEY_RE.fullmatch(instance_key):
        findings.append(
            _error(
                "instance_key_invalid",
                "INSTANCE_KEY must match ^[a-z0-9][a-z0-9-]*$ so stack, compose, and DB names stay predictable.",
            )
        )
    if instance_key == "base" and not _truthy(effective_env.get("ALLOW_BASE_INSTANCE")):
        findings.append(
            _error(
                "instance_key_base",
                "INSTANCE_KEY=base is blocked by default. Pick a real instance key or set ALLOW_BASE_INSTANCE=1 for an intentional disposable deployment.",
            )
        )

    effective_env.setdefault("GITHUB_OWNER", _OFFICIAL_GITHUB_OWNER)

    connection_arn = (effective_env.get("CONNECTION_ARN") or "").strip()
    if not connection_arn:
        findings.append(
            _error(
                "connection_arn_missing",
                "CONNECTION_ARN is required for the GitHub CodeConnections source action.",
            )
        )
    elif not connection_arn.startswith(_CODECONNECTIONS_ARN_PREFIX):
        findings.append(
            _error(
                "connection_arn_invalid",
                "CONNECTION_ARN must be an AWS CodeConnections ARN.",
            )
        )

    ssh_cidr = (effective_env.get("SSH_CIDR") or "").strip()
    if ssh_cidr in {"", "0.0.0.0/0"}:
        findings.append(
            _warning(
                "ssh_cidr_open",
                "SSH_CIDR is open to the world. Restrict it before creating a production-like instance.",
            )
        )

    overlay = load_instance_overlay(repo_root, instance_key)
    if overlay.instance_key != instance_key:
        findings.append(
            _error(
                "instance_key_mismatch",
                f"Resolved overlay INSTANCE_KEY={overlay.instance_key!r} does not match requested INSTANCE_KEY={instance_key!r}.",
            )
        )

    if overlay.public_host.endswith(".local") or overlay.public_host == "localhost":
        findings.append(
            _warning(
                "public_host_local",
                f"PUBLIC_HOST resolves to {overlay.public_host!r}. That is fine for a local/disposable instance, but not for an internet-facing deployment.",
            )
        )

    framework_invariants_path = repo_root / "framework_invariants.yaml"
    genesis_seed_path = repo_root / overlay.genesis_repo_path
    contracts_seed_path = repo_root / overlay.contracts_repo_path

    try:
        FrameworkInvariants.load(framework_invariants_path)
    except Exception as exc:
        findings.append(
            _error(
                "framework_invariants_invalid",
                f"Framework invariants are missing or invalid at {framework_invariants_path}: {exc}",
            )
        )

    try:
        Genesis.load(genesis_seed_path)
    except Exception as exc:
        findings.append(
            _error(
                "genesis_invalid",
                f"Genesis seed is missing or invalid at {genesis_seed_path}: {exc}",
            )
        )

    contract_findings, app_count = _validate_contracts(contracts_seed_path)
    findings.extend(contract_findings)

    if overlay.contracts_repo_path == "contracts.example.yaml" and app_count == 0:
        findings.append(
            _warning(
                "generic_contracts_baseline",
                "This deployment uses the tracked example contracts file. Copy and extend it once your instance has mounted apps or critical payload contracts.",
            )
        )

    return PreflightResult(findings=tuple(findings))


def _format_findings(findings: Iterable[Finding]) -> str:
    lines = []
    for finding in findings:
        prefix = "ERROR" if finding.level == "error" else "WARN "
        lines.append(f"[{prefix}] {finding.code}: {finding.message}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to validate. Defaults to the current checkout.",
    )
    args = parser.parse_args(argv)

    result = run_preflight(args.repo_root.resolve())

    if result.findings:
        print(_format_findings(result.findings))

    if result.errors:
        print(
            f"\nPreflight failed with {len(result.errors)} error(s) and {len(result.warnings)} warning(s).",
            file=sys.stderr,
        )
        return 1

    print(f"Preflight passed with {len(result.warnings)} warning(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
