"""Tests for runtime settings resolution and engine budgets."""

from app.system_settings import (
    build_default_system_settings,
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
