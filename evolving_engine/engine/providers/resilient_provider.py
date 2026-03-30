"""Provider wrapper that falls back across configured LLM backends when needed."""

from __future__ import annotations

from collections.abc import Callable

import structlog

from engine.config import EngineSettings
from engine.providers.anthropic_provider import AnthropicProvider
from engine.providers.base import BaseLLMProvider
from engine.providers.bedrock_provider import BedrockProvider
from engine.providers.openai_provider import OpenAIProvider

logger = structlog.get_logger()

_FAILOVER_ERROR_SNIPPETS = (
    "accessdenied",
    "access denied",
    "explicit deny",
    "not authorized",
    "unauthorized",
    "forbidden",
    "authentication",
    "invalid api key",
    "insufficient permissions",
    "permission",
    "unable to locate credentials",
    "rate limit",
    "throttl",
    "quota",
    "service unavailable",
    "sdk is unavailable",
)


class ResilientLLMProvider(BaseLLMProvider):
    """Try the preferred provider first, then fail over to configured alternatives."""

    def __init__(
        self,
        config: EngineSettings,
        provider_builders: dict[str, Callable[[EngineSettings], BaseLLMProvider]] | None = None,
    ) -> None:
        self.config = config
        self._preferred_provider = config.llm_provider
        self._providers: dict[str, BaseLLMProvider] = {}
        self._provider_builders = provider_builders or {
            "anthropic": AnthropicProvider,
            "bedrock": BedrockProvider,
            "openai": OpenAIProvider,
        }

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        model_override: str | None = None,
    ) -> str:
        """Generate text, retrying with another configured provider on auth/access failures."""
        order = self._provider_order()
        last_error: Exception | None = None

        for index, provider_name in enumerate(order):
            provider = self._provider(provider_name)
            try:
                text = await provider.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                    model_override=model_override,
                )
                if provider_name != self._preferred_provider:
                    logger.warning(
                        "llm_provider.fallback_success",
                        from_provider=self._preferred_provider,
                        to_provider=provider_name,
                    )
                self._preferred_provider = provider_name
                return text
            except Exception as exc:
                last_error = exc
                should_failover = index < len(order) - 1 and self._is_failover_candidate(exc)
                if not should_failover:
                    raise
                logger.warning(
                    "llm_provider.fallback_attempt",
                    provider=provider_name,
                    error=str(exc),
                )

        if last_error is not None:
            raise last_error
        raise RuntimeError("No LLM providers were configured")

    def _provider_order(self) -> list[str]:
        order = [self._preferred_provider]
        if self._preferred_provider != "anthropic" and self.config.anthropic_api_key.strip():
            order.append("anthropic")
        if self._preferred_provider != "openai" and self.config.openai_api_key.strip():
            order.append("openai")
        if self._preferred_provider != "bedrock" and self.config.bedrock_model_id.strip():
            order.append("bedrock")
        return order

    def _provider(self, provider_name: str) -> BaseLLMProvider:
        if provider_name not in self._providers:
            cfg = self.config.model_copy(deep=True)
            cfg.llm_provider = provider_name
            builder = self._provider_builders[provider_name]
            self._providers[provider_name] = builder(cfg)
        return self._providers[provider_name]

    @staticmethod
    def _is_failover_candidate(exc: Exception) -> bool:
        message = str(exc).strip().lower()
        return any(snippet in message for snippet in _FAILOVER_ERROR_SNIPPETS)
