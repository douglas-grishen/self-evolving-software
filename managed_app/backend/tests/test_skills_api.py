"""Tests for the runtime skills API handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1 import skills as skills_api
from app.schemas.skills import SkillInvocationRequest


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _ExecuteResult:
    def __init__(self, *, rows=None, scalar=None, tuples=None):
        self._rows = rows or []
        self._scalar = scalar
        self._tuples = tuples or []

    def scalars(self):
        return _ScalarRows(self._rows)

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return list(self._tuples)


class _FakeDB:
    def __init__(self, record, settings_rows=None):
        self.record = record
        self.settings_rows = settings_rows or []

    async def execute(self, statement):
        text = str(statement)
        if "FROM skills" in text and "WHERE skills.key =" in text:
            return _ExecuteResult(scalar=self.record)
        if "FROM skills" in text:
            return _ExecuteResult(rows=[self.record])
        if "FROM system_settings" in text:
            return _ExecuteResult(tuples=self.settings_rows)
        raise AssertionError(f"Unexpected SQL: {text}")


def _skill_record():
    timestamp = datetime.now(UTC)
    return SimpleNamespace(
        id="skill-1",
        key="web-browser",
        name="Web Browser",
        description="Structured browser automation",
        status="active",
        scope="engine_and_apps",
        executor_kind="local",
        config_json={"browser": "chromium"},
        permissions_json={"requires_enabled_setting": "skill_browser_enabled"},
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.mark.asyncio
async def test_list_skills_returns_persisted_registry_rows():
    """The skills index should project persisted skill metadata."""
    db = _FakeDB(_skill_record())

    response = await skills_api.list_skills(db)

    assert len(response) == 1
    assert response[0].key == "web-browser"
    assert response[0].executor_kind == "local"


@pytest.mark.asyncio
async def test_list_skills_can_return_send_email_entry():
    """The registry endpoint should expose additional skill records such as send-email."""
    record = _skill_record()
    record.key = "send-email"
    record.name = "Send Email"
    record.config_json = {"provider": "resend"}
    record.permissions_json = {"requires_secret_setting": "skill_email_resend_api_key"}
    db = _FakeDB(record)

    response = await skills_api.list_skills(db)

    assert response[0].key == "send-email"
    assert response[0].config_json["provider"] == "resend"


@pytest.mark.asyncio
async def test_get_skill_schema_returns_runtime_contract():
    """Schema reads should expose the typed input JSON schema for clients."""
    db = _FakeDB(_skill_record())

    response = await skills_api.get_skill_schema("web-browser", db)

    assert response.skill.key == "web-browser"
    assert response.input_schema["properties"]["actions"]["type"] == "array"


@pytest.mark.asyncio
async def test_invoke_skill_returns_403_when_runtime_toggle_is_off():
    """Disabled runtime toggles should be surfaced as permission errors."""
    db = _FakeDB(
        _skill_record(),
        settings_rows=[("skill_browser_enabled", "false")],
    )

    with pytest.raises(HTTPException) as exc_info:
        await skills_api.invoke_skill(
            "web-browser",
            SkillInvocationRequest(input={"actions": [{"type": "goto", "url": "https://example.com"}]}),
            db,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_invoke_skill_returns_422_for_invalid_payload():
    """Malformed inputs should fail before any browser work starts."""
    db = _FakeDB(
        _skill_record(),
        settings_rows=[("skill_browser_enabled", "true"), ("skill_browser_timeout_seconds", "15")],
    )

    with pytest.raises(HTTPException) as exc_info:
        await skills_api.invoke_skill(
            "web-browser",
            SkillInvocationRequest(input={"actions": [{"type": "wait_for"}]}),
            db,
        )

    assert exc_info.value.status_code == 422
