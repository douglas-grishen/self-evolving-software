"""OpenAI LLM provider — direct API integration via the official SDK."""

from __future__ import annotations

from typing import Any

import structlog

from engine.config import EngineSettings, settings
from engine.providers.base import BaseLLMProvider

logger = structlog.get_logger()


class OpenAIProvider(BaseLLMProvider):
    """LLM provider backed by OpenAI's Responses API."""

    def __init__(self, config: EngineSettings | None = None) -> None:
        cfg = config or settings
        try:
            from openai import AsyncOpenAI
        except Exception as exc:  # pragma: no cover - depends on installed environment
            raise RuntimeError(f"OpenAI SDK is unavailable: {exc}") from exc

        self.client: Any = AsyncOpenAI(api_key=cfg.openai_api_key)
        self.model = cfg.openai_model
        self.model_fast = cfg.openai_model_fast or cfg.openai_model

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        model_override: str | None = None,
    ) -> str:
        """Call the OpenAI Responses API and return the text response."""
        if model_override == "fast":
            model = self.model_fast
        elif model_override:
            model = model_override
        else:
            model = self.model

        if model_override == "fast" and model == self.model:
            max_tokens = min(max_tokens, 2048)

        logger.debug(
            "openai.generate",
            model=model,
            system_len=len(system_prompt),
            user_len=len(user_prompt),
            max_tokens=max_tokens,
        )

        response = await self.client.responses.create(
            model=model,
            instructions=system_prompt,
            input=user_prompt,
            max_output_tokens=max_tokens,
        )

        usage = getattr(response, "usage", None)
        logger.debug(
            "openai.response",
            model=model,
            request_id=getattr(response, "_request_id", None),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )

        return response.output_text
