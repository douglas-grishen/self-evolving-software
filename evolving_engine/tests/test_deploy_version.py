"""Tests for deploy-version parsing during local deploys."""

from engine.config import EngineSettings
from engine.deployer.git_ops import LocalDeployer


def test_increment_deploy_version_reads_typed_assignment(tmp_path):
    """Deploy version bumps should preserve and increment typed assignments."""
    deployer = LocalDeployer(EngineSettings(evolved_app_path=tmp_path))
    version_file = tmp_path / "backend" / "app" / "_deploy_version.py"
    version_file.parent.mkdir(parents=True)
    version_file.write_text("DEPLOY_VERSION: int = 7\n", encoding="utf-8")

    new_version = deployer._increment_deploy_version(tmp_path)

    assert new_version == 8
    assert "DEPLOY_VERSION: int = 8" in version_file.read_text(encoding="utf-8")
