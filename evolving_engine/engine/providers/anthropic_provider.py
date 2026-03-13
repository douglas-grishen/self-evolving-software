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
        )

        message = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Extract text from the first content block
        text = ""
        for block in message.content:
            if block.type == "text":
                text += block.text

        logger.debug(
            "anthropic.response",
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )

        return text
