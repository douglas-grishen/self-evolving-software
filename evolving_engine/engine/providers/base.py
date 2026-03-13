"""BaseLLMProvider — abstract interface for LLM backends."""

import json
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers.

    Supports two generation modes:
    - generate(): free-form text completion
    - generate_structured(): JSON output parsed into a Pydantic model
    """

    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
    ) -> str:
        """Generate a free-form text response."""

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        max_tokens: int = 4096,
    ) -> T:
        """Generate a response and parse it into a Pydantic model.

        The system prompt is augmented with JSON schema instructions.
        Subclasses may override this for native structured output support.
        """
        schema_json = json.dumps(response_model.model_json_schema(), indent=2)
        augmented_system = (
            f"{system_prompt}\n\n"
            f"You MUST respond with valid JSON matching this schema:\n"
            f"```json\n{schema_json}\n```\n"
            f"Respond ONLY with the JSON object, no extra text."
        )
        raw = await self.generate(augmented_system, user_prompt, max_tokens)

        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:]  # Remove opening fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]  # Remove closing fence
            cleaned = "\n".join(lines)

        return response_model.model_validate_json(cleaned)
