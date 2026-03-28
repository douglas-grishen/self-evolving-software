"""Regression tests for settings endpoints using projected columns."""

from datetime import datetime, timezone

import pytest

from app.api.v1 import settings as settings_api
from app.schemas.system_settings import SettingUpdate


@pytest.mark.asyncio
async def test_get_setting_reads_projected_columns_without_orm_row():
    """Key-specific reads should not require hydrating the full ORM model."""
    timestamp = datetime.now(timezone.utc)

    class _Result:
        def one_or_none(self):
            return ("engine_llm_provider", "openai", "Engine provider", timestamp)

    class _DB:
        async def execute(self, _statement):
            return _Result()

    response = await settings_api.get_setting("engine_llm_provider", _DB())

    assert response.key == "engine_llm_provider"
    assert response.value == "openai"
    assert response.description == "Engine provider"
    assert response.updated_at == timestamp


@pytest.mark.asyncio
async def test_update_setting_uses_projected_reload_after_update():
    """Updates should work without flush/refresh on a full ORM entity."""
    timestamp = datetime.now(timezone.utc)
    calls = []

    class _ScalarResult:
        def scalar_one_or_none(self):
            return "engine_llm_provider"

    class _RowResult:
        def one(self):
            return ("engine_llm_provider", "openai", "Engine provider", timestamp)

    class _DB:
        async def execute(self, statement):
            calls.append(str(statement))
            if len(calls) == 1:
                return _ScalarResult()
            if len(calls) == 2:
                return object()
            return _RowResult()

    response = await settings_api.update_setting(
        "engine_llm_provider",
        SettingUpdate(value="openai"),
        _DB(),
    )

    assert response.key == "engine_llm_provider"
    assert response.value == "openai"
    assert response.updated_at == timestamp
    assert len(calls) == 3
