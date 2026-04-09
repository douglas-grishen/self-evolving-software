"""Tests for optional Purpose loading semantics."""

from engine.models.purpose import Purpose


def test_load_optional_returns_none_for_empty_purpose_file(tmp_path):
    """Blank seeded files should mean 'purpose not defined yet'."""
    path = tmp_path / "purpose.yaml"
    path.write_text("")

    assert Purpose.load_optional(path) is None
