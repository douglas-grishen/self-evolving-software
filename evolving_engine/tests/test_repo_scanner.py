"""Tests for repository scanner helpers."""

from engine.repo.scanner import extract_alembic_revisions


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
