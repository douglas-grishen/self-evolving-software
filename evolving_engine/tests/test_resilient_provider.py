"""Tests for automatic provider failover in the engine."""

import pytest

from engine.config import EngineSettings
from engine.providers.base import BaseLLMProvider
from engine.providers.resilient_provider import ResilientLLMProvider


class _FailingProvider(BaseLLMProvider):
    def __init__(self, error: str, calls: list[str], name: str) -> None:
        self.error = error
        self.calls = calls
        self.name = name

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        model_override: str | None = None,
    ) -> str:
        self.calls.append(self.name)
        raise RuntimeError(self.error)


class _SuccessfulProvider(BaseLLMProvider):
    def __init__(self, response: str, calls: list[str], name: str) -> None:
        self.response = response
        self.calls = calls
        self.name = name

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        model_override: str | None = None,
    ) -> str:
        self.calls.append(self.name)
        return self.response


@pytest.mark.asyncio
async def test_resilient_provider_falls_back_from_bedrock_to_openai():
    """Engine should keep running when Bedrock is denied but OpenAI is configured."""
    calls: list[str] = []
    config = EngineSettings(
        llm_provider="bedrock",
        bedrock_model_id="global.anthropic.claude-sonnet-4-20250514-v1:0",
        openai_api_key="test-openai-key",
        openai_model="gpt-5.2",
    )
    provider = ResilientLLMProvider(
        config,
        provider_builders={
            "bedrock": lambda cfg: _FailingProvider(
                "AccessDeniedException: explicit deny in service control policy",
                calls,
                "bedrock",
            ),
            "openai": lambda cfg: _SuccessfulProvider("fallback-ok", calls, "openai"),
            "anthropic": lambda cfg: _SuccessfulProvider("unused", calls, "anthropic"),
        },
    )

    text = await provider.generate("system", "user")
    assert text == "fallback-ok"
    assert calls == ["bedrock", "openai"]

    # After a successful failover, the working provider should become sticky.
    text = await provider.generate("system", "user")
    assert text == "fallback-ok"
    assert calls == ["bedrock", "openai", "openai"]
