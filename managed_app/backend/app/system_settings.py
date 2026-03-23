"""Helpers for persisted runtime settings used by the engine and UI."""

from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_settings import SystemSetting

ALLOWED_LLM_PROVIDERS = {"anthropic", "bedrock", "openai"}
SECRET_SETTING_KEYS = {"anthropic_api_key", "openai_api_key"}
EDITABLE_SETTING_KEYS = {
    "proactive_interval_minutes",
    "llm_provider",
    "llm_model",
    "anthropic_api_key",
    "openai_api_key",
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


def build_default_system_settings() -> dict[str, tuple[str, str]]:
    """Return default persisted settings for first boot and missing-key repair."""
    provider = normalize_llm_provider(os.environ.get("ENGINE_LLM_PROVIDER"))
    return {
        "proactive_interval_minutes": (
            "60",
            "How often the engine runs proactive evolution (minutes). Min: 5, Max: 1440.",
        ),
        "llm_provider": (
            provider,
            "Active LLM provider for the engine and chat runtime: anthropic, bedrock, or openai.",
        ),
        "llm_model": (
            default_model_for_provider(provider),
            "Active model identifier for the selected LLM provider.",
        ),
        "anthropic_api_key": (
            "",
            "Override for the Anthropic API key. Leave blank to use ENGINE_ANTHROPIC_API_KEY.",
        ),
        "openai_api_key": (
            "",
            "Override for the OpenAI API key. Leave blank to use ENGINE_OPENAI_API_KEY.",
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

    changed = False
    for key, (value, description) in defaults.items():
        record = existing.get(key)
        if record is None:
            db.add(SystemSetting(key=key, value=value, description=description))
            changed = True
            continue
        if not record.description:
            record.description = description
            changed = True

    if changed:
        await db.commit()
