"""Tests for deploy preflight validation."""

import importlib
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

preflight_instance = importlib.import_module("preflight_instance")
run_preflight = preflight_instance.run_preflight


def test_preflight_blocks_base_instance_without_explicit_override():
    result = run_preflight(
        REPO_ROOT,
        {
            "INSTANCE_KEY": "base",
            "GITHUB_OWNER": "example-owner",
            "CONNECTION_ARN": "arn:aws:codeconnections:us-east-1:123456789012:connection/example",
            "SKIP_GIT_SOURCE_CHECKS": "1",
        },
    )

    assert any(finding.code == "instance_key_base" for finding in result.errors)


def test_preflight_accepts_custom_instance_key_without_overlay():
    result = run_preflight(
        REPO_ROOT,
        {
            "INSTANCE_KEY": "demo-instance",
            "GITHUB_OWNER": "example-owner",
            "CONNECTION_ARN": "arn:aws:codeconnections:us-east-1:123456789012:connection/example",
            "SSH_CIDR": "203.0.113.10/32",
            "SKIP_GIT_SOURCE_CHECKS": "1",
        },
    )

    assert result.errors == ()
    warning_codes = {finding.code for finding in result.warnings}
    assert "empty_runtime_contracts" in warning_codes


def test_preflight_defaults_to_official_github_owner_when_missing():
    result = run_preflight(
        REPO_ROOT,
        {
            "INSTANCE_KEY": "demo-instance",
            "CONNECTION_ARN": "arn:aws:codeconnections:us-east-1:123456789012:connection/example",
            "SSH_CIDR": "203.0.113.10/32",
            "SKIP_GIT_SOURCE_CHECKS": "1",
        },
    )

    assert result.errors == ()


def test_preflight_rejects_invalid_instance_key():
    result = run_preflight(
        REPO_ROOT,
        {
            "INSTANCE_KEY": "Bad Key",
            "GITHUB_OWNER": "example-owner",
            "CONNECTION_ARN": "arn:aws:codeconnections:us-east-1:123456789012:connection/example",
            "SKIP_GIT_SOURCE_CHECKS": "1",
        },
    )

    assert any(finding.code == "instance_key_invalid" for finding in result.errors)


def test_preflight_rejects_legacy_purpose_seed_checkout(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "stacks").mkdir()
    (tmp_path / "evolving_engine" / "engine").mkdir(parents=True)
    (tmp_path / "appspec.yml").write_text("version: 0.0\nfiles:\n  - source: /\n    destination: /opt/self-evolving-software\n")
    (tmp_path / "docker-compose.prod.yml").write_text("services:\n  engine:\n    environment:\n      ENGINE_PURPOSE_SEED_PATH: /workspace/purpose.yaml\n")
    (tmp_path / "evolving_engine" / "engine" / "config.py").write_text("purpose_seed_path = 'legacy'\n")
    (tmp_path / "infra" / "stacks" / "ec2_stack.py").write_text(
        'git clone --branch main https://github.com/example/repo.git "$FRAMEWORK_ROOT"\n'
        'docker compose -p "$COMPOSE_PROJECT" -f "$FRAMEWORK_ROOT/docker-compose.prod.yml" up -d --build\n'
    )
    (tmp_path / "framework_invariants.yaml").write_text((REPO_ROOT / "framework_invariants.yaml").read_text())
    (tmp_path / "genesis.yaml").write_text((REPO_ROOT / "genesis.yaml").read_text())
    (tmp_path / "contracts.example.yaml").write_text((REPO_ROOT / "contracts.example.yaml").read_text())
    (tmp_path / "purpose.yaml").write_text("version: 1\n")

    result = run_preflight(
        tmp_path,
        {
            "INSTANCE_KEY": "demo-instance",
            "CONNECTION_ARN": "arn:aws:codeconnections:us-east-1:123456789012:connection/example",
            "SKIP_GIT_SOURCE_CHECKS": "1",
        },
    )

    error_codes = {finding.code for finding in result.errors}
    assert "legacy_purpose_seed_present" in error_codes
    assert "legacy_purpose_seed_env" in error_codes
    assert "legacy_purpose_seed_config" in error_codes
    assert "legacy_appspec_destination" in error_codes
    assert "bootstrap_clones_github_source" in error_codes
    assert "bootstrap_starts_services_early" in error_codes


def test_preflight_rejects_tracked_product_apps_in_framework_checkout(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "stacks").mkdir()
    (tmp_path / "evolving_engine" / "engine").mkdir(parents=True)
    (tmp_path / "managed_app" / "frontend" / "src" / "apps" / "competitive-intelligence").mkdir(
        parents=True
    )
    (tmp_path / "appspec.yml").write_text("version: 0.0\n")
    (tmp_path / "docker-compose.prod.yml").write_text("services:\n  engine:\n    environment: {}\n")
    (tmp_path / "evolving_engine" / "engine" / "config.py").write_text("class Settings: pass\n")
    (tmp_path / "infra" / "stacks" / "ec2_stack.py").write_text("")
    (tmp_path / "framework_invariants.yaml").write_text((REPO_ROOT / "framework_invariants.yaml").read_text())
    (tmp_path / "genesis.yaml").write_text((REPO_ROOT / "genesis.yaml").read_text())
    (tmp_path / "contracts.example.yaml").write_text((REPO_ROOT / "contracts.example.yaml").read_text())

    result = run_preflight(
        tmp_path,
        {
            "INSTANCE_KEY": "demo-instance",
            "CONNECTION_ARN": "arn:aws:codeconnections:us-east-1:123456789012:connection/example",
            "SKIP_GIT_SOURCE_CHECKS": "1",
        },
    )

    error_codes = {finding.code for finding in result.errors}
    assert "tracked_product_apps_present" in error_codes


def test_preflight_rejects_deploy_source_branch_mismatch(monkeypatch):
    responses = {
        ("status", "--short"): "",
        ("remote", "get-url", "origin"): "https://github.com/douglas-grishen/self-evolving-software.git",
        ("branch", "--show-current"): "codex/fix-purpose",
        ("rev-parse", "HEAD"): "abc123",
        ("ls-remote", "origin", "refs/heads/main"): "def456\trefs/heads/main",
    }

    monkeypatch.setattr(
        preflight_instance,
        "_git_output",
        lambda _repo_root, *args: responses.get(args),
    )

    result = run_preflight(
        REPO_ROOT,
        {
            "INSTANCE_KEY": "demo-instance",
            "GITHUB_OWNER": "douglas-grishen",
            "GITHUB_REPO": "self-evolving-software",
            "GITHUB_BRANCH": "main",
            "CONNECTION_ARN": "arn:aws:codeconnections:us-east-1:123456789012:connection/example",
        },
    )

    error_codes = {finding.code for finding in result.errors}
    assert "deploy_source_branch_mismatch" in error_codes
    assert "deploy_source_remote_drift" in error_codes
