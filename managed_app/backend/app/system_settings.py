"""Helpers for persisted runtime settings used by the engine and UI."""

from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_settings import SystemSetting

ALLOWED_LLM_PROVIDERS = {"anthropic", "bedrock", "openai"}
SECRET_SETTING_KEYS = {"anthropic_api_key", "openai_api_key"}
LEGACY_LLM_PROVIDER_KEY = "llm_provider"
LEGACY_LLM_MODEL_KEY = "llm_model"
CHAT_LLM_PROVIDER_KEY = "chat_llm_provider"
CHAT_LLM_MODEL_KEY = "chat_llm_model"
ENGINE_LLM_PROVIDER_KEY = "engine_llm_provider"
ENGINE_LLM_MODEL_KEY = "engine_llm_model"
ENGINE_DAILY_LLM_CALLS_LIMIT_KEY = "engine_daily_llm_calls_limit"
ENGINE_DAILY_INPUT_TOKENS_LIMIT_KEY = "engine_daily_input_tokens_limit"
ENGINE_DAILY_OUTPUT_TOKENS_LIMIT_KEY = "engine_daily_output_tokens_limit"
ENGINE_DAILY_PROACTIVE_RUNS_LIMIT_KEY = "engine_daily_proactive_runs_limit"
ENGINE_DAILY_FAILED_EVOLUTIONS_LIMIT_KEY = "engine_daily_failed_evolutions_limit"
ENGINE_DAILY_TASK_ATTEMPT_LIMIT_KEY = "engine_daily_task_attempt_limit"
ENGINE_DAILY_USAGE_SNAPSHOT_KEY = "engine_daily_usage_snapshot"
PROVIDER_SETTING_KEYS = {
    LEGACY_LLM_PROVIDER_KEY,
    CHAT_LLM_PROVIDER_KEY,
    ENGINE_LLM_PROVIDER_KEY,
}
MODEL_SETTING_KEYS = {
    LEGACY_LLM_MODEL_KEY,
    CHAT_LLM_MODEL_KEY,
    ENGINE_LLM_MODEL_KEY,
}
ENGINE_BUDGET_SETTING_KEYS = {
    ENGINE_DAILY_LLM_CALLS_LIMIT_KEY,
    ENGINE_DAILY_INPUT_TOKENS_LIMIT_KEY,
    ENGINE_DAILY_OUTPUT_TOKENS_LIMIT_KEY,
    ENGINE_DAILY_PROACTIVE_RUNS_LIMIT_KEY,
    ENGINE_DAILY_FAILED_EVOLUTIONS_LIMIT_KEY,
    ENGINE_DAILY_TASK_ATTEMPT_LIMIT_KEY,
}
EDITABLE_SETTING_KEYS = {
    "proactive_interval_minutes",
    *PROVIDER_SETTING_KEYS,
    *MODEL_SETTING_KEYS,
    *ENGINE_BUDGET_SETTING_KEYS,
    "anthropic_api_key",
    "openai_api_key",
    ENGINE_DAILY_USAGE_SNAPSHOT_KEY,
}


def normalize_llm_provider(value: str | None) -> str:
    """Normalize provider names and fall back to Anthropic on invalid input."""
    provider = (value or "").strip().lower()
    if provider in ALLOWED_LLM_PROVIDERS:
        return provider
    return "anthropic"


def default_model_for_provider(provider: str) -> str:
    """Return the env-backed default model identifier for a provider."""
    normalized = normalize_llm_provider(provider)
    if normalized == "bedrock":
        return os.environ.get(
            "ENGINE_BEDROCK_MODEL_ID",
            "global.anthropic.claude-sonnet-4-20250514-v1:0",
        )
    if normalized == "openai":
        return os.environ.get("ENGINE_OPENAI_MODEL", "gpt-5.2")
    return os.environ.get("ENGINE_ANTHROPIC_MODEL", "claude-sonnet-4-20250514")


def default_budget_value(env_key: str, fallback: int, *, minimum: int = 1) -> str:
    """Return a normalized positive integer budget as a persisted string."""
    raw = os.environ.get(env_key, str(fallback))
    try:
        value = int(raw)
    except ValueError:
        value = fallback
    return str(max(minimum, value))


def resolve_runtime_provider(
    values: dict[str, str],
    scope: str,
    *,
    fallback_provider: str | None = None,
) -> str:
    """Resolve chat/engine provider with scoped override and legacy fallback."""
    scoped_key = CHAT_LLM_PROVIDER_KEY if scope == "chat" else ENGINE_LLM_PROVIDER_KEY
    selected = values.get(scoped_key) or values.get(LEGACY_LLM_PROVIDER_KEY) or fallback_provider
    return normalize_llm_provider(selected)


def resolve_runtime_model(
    values: dict[str, str],
    scope: str,
    provider: str,
) -> str:
    """Resolve chat/engine model with scoped override and legacy fallback."""
    scoped_key = CHAT_LLM_MODEL_KEY if scope == "chat" else ENGINE_LLM_MODEL_KEY
    selected = (values.get(scoped_key) or "").strip()
    if selected:
        return selected

    legacy = (values.get(LEGACY_LLM_MODEL_KEY) or "").strip()
    if legacy:
        return legacy

    return default_model_for_provider(provider)


def build_default_system_settings() -> dict[str, tuple[str, str]]:
    """Return default persisted settings for first boot and missing-key repair."""
    provider = normalize_llm_provider(os.environ.get("ENGINE_LLM_PROVIDER"))
    default_model = default_model_for_provider(provider)
    return {
        "proactive_interval_minutes": (
            "60",
            "How often the engine runs proactive evolution (minutes). Min: 5, Max: 1440.",
        ),
        LEGACY_LLM_PROVIDER_KEY: (
            provider,
            "Legacy shared LLM provider fallback. New installs should prefer chat_llm_provider and engine_llm_provider.",
        ),
        LEGACY_LLM_MODEL_KEY: (
            default_model,
            "Legacy shared model fallback. New installs should prefer chat_llm_model and engine_llm_model.",
        ),
        CHAT_LLM_PROVIDER_KEY: (
            provider,
            "Active LLM provider for chat runtime: anthropic, bedrock, or openai.",
        ),
        CHAT_LLM_MODEL_KEY: (
            default_model,
            "Active model identifier for the chat runtime provider.",
        ),
        ENGINE_LLM_PROVIDER_KEY: (
            provider,
            "Active LLM provider for the self-evolution engine: anthropic, bedrock, or openai.",
        ),
        ENGINE_LLM_MODEL_KEY: (
            default_model,
            "Active model identifier for the self-evolution engine provider.",
        ),
        "anthropic_api_key": (
            "",
            "Override for the Anthropic API key. Leave blank to use ENGINE_ANTHROPIC_API_KEY.",
        ),
        "openai_api_key": (
            "",
            "Override for the OpenAI API key. Leave blank to use ENGINE_OPENAI_API_KEY.",
        ),
        ENGINE_DAILY_LLM_CALLS_LIMIT_KEY: (
            default_budget_value("ENGINE_DAILY_LLM_CALLS_LIMIT", 60),
            "UTC daily limit for self-evolution LLM calls before proactive work enters safe mode.",
        ),
        ENGINE_DAILY_INPUT_TOKENS_LIMIT_KEY: (
            default_budget_value("ENGINE_DAILY_INPUT_TOKENS_LIMIT", 500000),
            "UTC daily limit for self-evolution input tokens before proactive work enters safe mode.",
        ),
        ENGINE_DAILY_OUTPUT_TOKENS_LIMIT_KEY: (
            default_budget_value("ENGINE_DAILY_OUTPUT_TOKENS_LIMIT", 120000),
            "UTC daily limit for self-evolution output tokens before proactive work enters safe mode.",
        ),
        ENGINE_DAILY_PROACTIVE_RUNS_LIMIT_KEY: (
            default_budget_value("ENGINE_DAILY_PROACTIVE_RUNS_LIMIT", 24),
            "UTC daily limit for proactive engine runs before it stops autonomous product work.",
        ),
        ENGINE_DAILY_FAILED_EVOLUTIONS_LIMIT_KEY: (
            default_budget_value("ENGINE_DAILY_FAILED_EVOLUTIONS_LIMIT", 10),
            "UTC daily limit for failed proactive evolutions before the engine enters safe mode.",
        ),
        ENGINE_DAILY_TASK_ATTEMPT_LIMIT_KEY: (
            default_budget_value("ENGINE_DAILY_TASK_ATTEMPT_LIMIT", 3),
            "UTC daily cap for how many times the engine may start the same backlog task before moving on.",
        ),
        ENGINE_DAILY_USAGE_SNAPSHOT_KEY: (
            "{}",
            "Engine-maintained UTC daily usage snapshot for LLM calls, tokens, and proactive runs.",
        ),
    }


def mask_setting_value(key: str, value: str) -> str:
    """Mask secret settings while preserving the configured/not-configured signal."""
    if key not in SECRET_SETTING_KEYS or not value:
        return value
    return "*" * max(0, len(value) - 4) + value[-4:]


async def ensure_default_system_settings(db: AsyncSession) -> None:
    """Backfill missing runtime settings for existing deployments."""
    defaults = build_default_system_settings()
    result = await db.execute(select(SystemSetting))
    existing = {setting.key: setting for setting in result.scalars().all()}
    existing_values = {key: record.value for key, record in existing.items()}

    changed = False
    for key, (value, description) in defaults.items():
        record = existing.get(key)
        if record is None:
            if key in {CHAT_LLM_PROVIDER_KEY, ENGINE_LLM_PROVIDER_KEY}:
                value = normalize_llm_provider(
                    existing_values.get(LEGACY_LLM_PROVIDER_KEY) or value
                )
            elif key in {CHAT_LLM_MODEL_KEY, ENGINE_LLM_MODEL_KEY}:
                legacy_model = (existing_values.get(LEGACY_LLM_MODEL_KEY) or "").strip()
                if legacy_model:
                    value = legacy_model
                else:
                    scoped_provider_fallback = defaults[
                        CHAT_LLM_PROVIDER_KEY if key == CHAT_LLM_MODEL_KEY else ENGINE_LLM_PROVIDER_KEY
                    ][0]
                    scoped_provider = resolve_runtime_provider(
                        existing_values,
                        "chat" if key == CHAT_LLM_MODEL_KEY else "engine",
                        fallback_provider=scoped_provider_fallback,
                    )
                    value = default_model_for_provider(scoped_provider)

            db.add(SystemSetting(key=key, value=value, description=description))
            existing_values[key] = value
            changed = True
            continue
        if not record.description:
            record.description = description
            changed = True

    if changed:
        await db.commit()
