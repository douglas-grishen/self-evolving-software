"""Anthropic Claude LLM provider — direct API integration.

Supports two model tiers:
- self.model (default): High-capability model for code generation (Sonnet)
- self.model_fast: Fast/cheap model for analysis and planning (Haiku)

Callers can pass model_override to generate() to use the fast model.
"""

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
        self.model_fast = cfg.anthropic_model_fast

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        model_override: str | None = None,
    ) -> str:
        """Call the Anthropic Messages API and return the text response.

        Args:
            model_override: If set, use this model instead of the default.
                            Pass "fast" to use the fast/cheap model (Haiku).

        Uses streaming for large token requests (>16K) as required by the SDK.
        """
        # Resolve model
        if model_override == "fast":
            model = self.model_fast
        elif model_override:
            model = model_override
        else:
            model = self.model

        logger.debug(
            "anthropic.generate",
            model=model,
            system_len=len(system_prompt),
            user_len=len(user_prompt),
            max_tokens=max_tokens,
        )

        # Use extended output beta for large token requests (>16K)
        extra_headers = {}
        if max_tokens > 16384:
            extra_headers["anthropic-beta"] = "output-128k-2025-02-19"

        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if extra_headers:
            kwargs["extra_headers"] = extra_headers

        # SDK requires streaming for requests that may take >10 minutes
        # (typically when max_tokens > 16384). Use streaming for all large requests.
        if max_tokens > 16384:
            return await self._generate_streaming(**kwargs)

        message = await self.client.messages.create(**kwargs)

        # Extract text from the first content block
        text = ""
        for block in message.content:
            if block.type == "text":
                text += block.text

        logger.debug(
            "anthropic.response",
            model=model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            stop_reason=message.stop_reason,
        )

        return text

    async def _generate_streaming(self, **kwargs) -> str:
        """Stream a response from the Anthropic API, collecting all text.

        Required for extended output (>16K tokens) to avoid SDK timeout errors.
        """
        text_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0
        stop_reason = None

        async with self.client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if hasattr(event, "type"):
                    if event.type == "content_block_delta" and hasattr(event, "delta"):
                        if hasattr(event.delta, "text"):
                            text_parts.append(event.delta.text)

            # Get final message for usage stats
            final_message = await stream.get_final_message()
            input_tokens = final_message.usage.input_tokens
            output_tokens = final_message.usage.output_tokens
            stop_reason = final_message.stop_reason

        text = "".join(text_parts)

        logger.debug(
            "anthropic.response",
            model=kwargs.get("model", "?"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=stop_reason,
            streamed=True,
        )

        return text
