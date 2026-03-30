"""Tests for framework invariants loading and prompt formatting."""

from engine.models.framework_invariants import FrameworkInvariants


def test_framework_invariants_load_and_format(tmp_path):
    path = tmp_path / "framework_invariants.yaml"
    path.write_text(
        """
version: 1
updated_at: "2026-03-29T00:00:00Z"
identity:
  name: "Framework"
  description: "Shared rules."
platform_invariants:
  - "Keep shell stable."
safety_invariants:
  - "Validate in sandbox."
operator_invariants:
  - "First Purpose comes from the UI."
evolution_invariants:
  - "Prefer incremental changes."
""".strip(),
        encoding="utf-8",
    )

    invariants = FrameworkInvariants.load(path)
    prompt = invariants.to_prompt_context()

    assert invariants.identity.name == "Framework"
    assert "Framework Invariants (v1)" in prompt
    assert "First Purpose comes from the UI." in prompt
