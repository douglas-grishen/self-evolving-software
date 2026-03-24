"""Tests for repository scanner helpers."""

from engine.repo.scanner import (
    build_repo_map,
    canonicalize_frontend_app_key,
    extract_alembic_revisions,
    extract_frontend_app_modules,
)


def test_extract_alembic_revisions_reads_revision_chain(tmp_path):
    """Scanner should expose Alembic lineage for prompt context."""
    versions_dir = tmp_path / "backend" / "alembic" / "versions"
    versions_dir.mkdir(parents=True)
    (versions_dir / "001_initial.py").write_text(
        'revision = "001_initial"\n'
        "down_revision = None\n"
    )
    (versions_dir / "002_next.py").write_text(
        'revision = "002_next"\n'
        'down_revision = "001_initial"\n'
    )

    revisions = extract_alembic_revisions(tmp_path)

    assert revisions == [
        "001_initial -> None (001_initial.py)",
        "002_next -> 001_initial (002_next.py)",
    ]


def test_canonicalize_frontend_app_key_slugifies_camel_case():
    """Frontend module names should resolve to a stable desktop slug."""
    assert canonicalize_frontend_app_key("CompetitiveIntelligence") == "competitive-intelligence"
    assert canonicalize_frontend_app_key("Competitive Intelligence") == "competitive-intelligence"


def test_extract_frontend_app_modules_detects_case_conflicts(tmp_path):
    """Scanner should expose duplicate app roots that only differ by casing/separators."""
    apps_dir = tmp_path / "frontend" / "src" / "apps"
    (apps_dir / "CompanyDiscovery").mkdir(parents=True)
    (apps_dir / "CompanyDiscovery" / "index.tsx").write_text("export default function A() {}")
    (apps_dir / "company-discovery").mkdir(parents=True)
    (apps_dir / "company-discovery" / "index.tsx").write_text("export default function B() {}")

    modules, conflicts = extract_frontend_app_modules(tmp_path / "frontend")

    assert {module.relative_path for module in modules} == {
        "frontend/src/apps/CompanyDiscovery",
        "frontend/src/apps/company-discovery",
    }
    assert len(conflicts) == 1
    assert conflicts[0].canonical_key == "company-discovery"
    assert conflicts[0].paths == [
        "frontend/src/apps/CompanyDiscovery",
        "frontend/src/apps/company-discovery",
    ]


def test_build_repo_map_surfaces_notable_public_assets(tmp_path):
    """Large public assets should appear in the repo map context."""
    public_dir = tmp_path / "frontend" / "public"
    public_dir.mkdir(parents=True)
    (public_dir / "genesis-bg.jpg").write_bytes(b"x" * 300_000)

    repo_map = build_repo_map(tmp_path)

    assert repo_map.public_assets
    assert repo_map.public_assets[0].relative_path == "frontend/public/genesis-bg.jpg"
