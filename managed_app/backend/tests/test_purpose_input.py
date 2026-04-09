"""Tests for Purpose creation from a single free-text input."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.api.v1.evolution import _build_purpose_yaml_from_text, create_purpose
from app.models.evolution import PurposeRecord
from app.schemas.evolution import PurposeCreate


class _ExecuteResult:
    def __init__(self, record=None):
        self._record = record

    def scalar_one_or_none(self):
        return self._record


class _FakePurposeSession:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []

    async def execute(self, stmt):
        return _ExecuteResult(self.existing)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        target = self.added[-1] if self.added else self.existing
        if target is None:
            return
        if getattr(target, "id", None) is None:
            target.id = "purpose-1"
        target.created_at = target.created_at or datetime.now(UTC)


def test_purpose_create_requires_exactly_one_input_format():
    with pytest.raises(ValidationError):
        PurposeCreate(version=1)

    with pytest.raises(ValidationError):
        PurposeCreate(version=1, content_yaml="version: 1\n", purpose_text="plain text")


def test_build_purpose_yaml_from_text_uses_generic_structure_and_extracts_bullets():
    yaml_text = _build_purpose_yaml_from_text(
        "Operations workspace for a law firm.\n- Keep a full audit trail.\n- Protect client data.",
        version=1,
    )

    assert 'name: "Operations workspace for a law firm"' in yaml_text
    assert (
        'description: "Operations workspace for a law firm. Keep a full audit trail. '
        'Protect client data."'
    ) in yaml_text
    assert '  - "Keep a full audit trail."' in yaml_text
    assert '  - "Protect client data."' in yaml_text
    assert "technical_requirements: []" in yaml_text


def test_build_purpose_yaml_from_text_uses_heading_as_name_without_duplication():
    yaml_text = _build_purpose_yaml_from_text(
        (
            "Core Purpose\n\n"
            "Design, improve, and continuously evolve an autonomous software platform.\n\n"
            "⸻\n\n"
            "Short Version\n\n"
            "Continuously evolve into a public web platform."
        ),
        version=1,
    )

    assert 'name: "Core Purpose"' in yaml_text
    assert (
        'description: "Design, improve, and continuously evolve an autonomous '
        'software platform. Short Version Continuously evolve into a public '
        'web platform."'
    ) in yaml_text
    assert 'description: "Core Purpose Design' not in yaml_text


@pytest.mark.asyncio
async def test_create_purpose_converts_plain_text_before_persisting():
    db = _FakePurposeSession()

    record = await create_purpose(
        PurposeCreate(
            version=1,
            purpose_text=(
                "This system should help independent clinics manage scheduling and billing "
                "while keeping strong operator controls."
            ),
        ),
        db=db,
    )

    assert isinstance(record, PurposeRecord)
    assert record.id == "purpose-1"
    assert record.version == 1
    assert 'name: "User-Defined Purpose"' in record.content_yaml
    assert "Purpose YAML" not in record.content_yaml
    assert (
        'description: "This system should help independent clinics manage scheduling '
        'and billing while keeping strong operator controls."'
    ) in record.content_yaml
