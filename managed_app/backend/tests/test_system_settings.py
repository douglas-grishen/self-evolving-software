"""Tests for runtime settings resolution and engine budgets."""

import pytest

from app.system_settings import (
    build_default_system_settings,
    ensure_default_system_settings,
    repair_legacy_budget_value,
    resolve_runtime_model,
    resolve_runtime_provider,
)


def test_resolve_runtime_provider_prefers_scope_specific_value():
    values = {
        "llm_provider": "openai",
        "chat_llm_provider": "anthropic",
        "engine_llm_provider": "bedrock",
    }

    assert resolve_runtime_provider(values, "chat", fallback_provider="openai") == "anthropic"
    assert resolve_runtime_provider(values, "engine", fallback_provider="openai") == "bedrock"


def test_resolve_runtime_model_falls_back_to_legacy_shared_value():
    values = {
        "llm_model": "gpt-5.3-codex",
        "chat_llm_provider": "openai",
    }

    assert resolve_runtime_model(values, "chat", "openai") == "gpt-5.3-codex"
    assert resolve_runtime_model(values, "engine", "openai") == "gpt-5.3-codex"


def test_default_settings_include_engine_budget_keys():
    defaults = build_default_system_settings()

    for key in (
        "engine_daily_llm_calls_limit",
        "engine_daily_input_tokens_limit",
        "engine_daily_output_tokens_limit",
        "engine_daily_proactive_runs_limit",
        "engine_daily_failed_evolutions_limit",
        "engine_daily_task_attempt_limit",
        "engine_daily_usage_snapshot",
    ):
        assert key in defaults


def test_default_settings_raise_recommended_engine_budgets():
    defaults = build_default_system_settings()

    assert defaults["engine_daily_llm_calls_limit"][0] == "240"
    assert defaults["engine_daily_input_tokens_limit"][0] == "1500000"
    assert defaults["engine_daily_output_tokens_limit"][0] == "250000"


def test_repair_legacy_budget_value_only_updates_old_defaults():
    assert repair_legacy_budget_value("engine_daily_llm_calls_limit", "60") == "240"
    assert repair_legacy_budget_value("engine_daily_input_tokens_limit", "500000") == "1500000"
    assert repair_legacy_budget_value("engine_daily_output_tokens_limit", "120000") == "250000"
    assert repair_legacy_budget_value("engine_daily_llm_calls_limit", "500") == "500"


@pytest.mark.asyncio
async def test_ensure_default_system_settings_projects_only_live_columns():
    """Startup repair should avoid selecting or writing columns missing in the live table."""
    executed = []

    class _Row:
        def __init__(self, mapping):
            self._mapping = mapping

    class _Result:
        def all(self):
            return [_Row({"key": "llm_provider", "value": "openai"})]

    class _Connection:
        async def run_sync(self, fn):
            return {"id", "key", "value"}

    class _DB:
        committed = False

        async def connection(self):
            return _Connection()

        async def execute(self, statement):
            executed.append(statement)
            if len(executed) == 1:
                return _Result()
            return object()

        async def commit(self):
            self.committed = True

    db = _DB()
    await ensure_default_system_settings(db)

    assert [column.key for column in executed[0].selected_columns] == ["key", "value"]
    insert_statements = [
        statement for statement in executed[1:] if statement.__class__.__name__ == "Insert"
    ]
    assert insert_statements
    assert all("description" not in statement.compile().params for statement in insert_statements)
    assert db.committed is True


@pytest.mark.asyncio
async def test_ensure_default_system_settings_skips_when_table_missing():
    """Startup should not crash the whole backend when an old instance lacks the table."""

    class _Connection:
        async def run_sync(self, fn):
            return set()

    class _DB:
        committed = False
        executed = False

        async def connection(self):
            return _Connection()

        async def execute(self, statement):  # pragma: no cover - regression guard
            self.executed = True
            return object()

        async def commit(self):
            self.committed = True

    db = _DB()
    await ensure_default_system_settings(db)

    assert db.executed is False
    assert db.committed is False
