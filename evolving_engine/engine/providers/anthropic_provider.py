"""Anthropic Claude LLM provider — direct API integration."""

import anthropic
import structlog

from engine.config import EngineSettings, settings
from engine.providers.base import BaseLLMProvider

logger = structlog.get_logger()


class AnthropicProvider(BaseLLMProvider):
    """LLM provider backed by the Anthropic Messages API."""

    def __init__(self, config: EngineSettings | None = None) -> None:
        cfg = config or settings
        self.client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
        self.model = cfg.anthropic_model

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
    ) -> str:
        """Call the Anthropic Messages API and return the text response."""
        logger.debug(
            "anthropic.generate",
            model=self.model,
            system_len=len(system_prompt),
            user_len=len(user_prompt),
            max_tokens=max_tokens,
        )

        # Use extended output beta for large token requests (>16K)
        # Required for models like claude-sonnet-4-5 to output more than 16384 tokens
        extra_headers = {}
        if max_tokens > 16384:
            extra_headers["anthropic-beta"] = "output-128k-2025-02-19"

        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if extra_headers:
            kwargs["extra_headers"] = extra_headers

        message = await self.client.messages.create(**kwargs)

        # Extract text from the first content block
        text = ""
        for block in message.content:
            if block.type == "text":
                text += block.text

        logger.debug(
            "anthropic.response",
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            stop_reason=message.stop_reason,
        )

        return text
