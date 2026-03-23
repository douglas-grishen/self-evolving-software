"""Tests for split chat/engine runtime settings resolution."""

from app.system_settings import resolve_runtime_model, resolve_runtime_provider


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
