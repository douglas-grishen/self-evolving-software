"""BaseLLMProvider — abstract interface for LLM backends."""

import json
import logging
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


def _repair_truncated_json(text: str) -> str:
    """Attempt to repair JSON that was truncated mid-generation.

    The LLM may hit max_tokens and produce incomplete JSON.
    This function tries to close open brackets/braces to make it parseable,
    even if it means losing the last incomplete file entry.
    """
    # Strategy: find the last complete object in the files array
    # by looking for the pattern `}, {` or `}]` and closing there
    text = text.rstrip()

    # If it already ends with proper closing, nothing to do
    if text.endswith("}"):
        return text

    # Try to find the last complete "content" field closure
    # Pattern: look for the last `"}` followed by incomplete data
    # and try to close the array and object

    # Find the last complete file object boundary
    # Each file entry ends with `}` (closing the file object)
    # The array ends with `]` and the wrapper ends with `}`

    # Strategy 1: Find the last `},` or `}` that could be a file boundary
    last_complete = -1
    brace_depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            brace_depth += 1
        elif ch == '}':
            brace_depth -= 1
            if brace_depth == 1:  # Just closed a file object inside the files array
                last_complete = i

    if last_complete > 0:
        # Truncate to last complete file object, close the array and wrapper
        repaired = text[:last_complete + 1] + "\n  ]\n}"
        return repaired

    return text


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
        model_override: str | None = None,
    ) -> str:
        """Generate a free-form text response.

        Args:
            model_override: If set, use this model instead of the default.
                            Pass "fast" to use a cheaper model for analysis/planning.
        """

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        max_tokens: int = 4096,
        retries: int = 2,
        model_override: str | None = None,
    ) -> T:
        """Generate a response and parse it into a Pydantic model.

        The system prompt is augmented with JSON schema instructions.
        Includes automatic retry with truncation repair.
        """
        schema_json = json.dumps(response_model.model_json_schema(), indent=2)
        augmented_system = (
            f"{system_prompt}\n\n"
            f"You MUST respond with valid JSON matching this schema:\n"
            f"```json\n{schema_json}\n```\n"
            f"Respond ONLY with the JSON object, no extra text."
        )

        last_error = None
        for attempt in range(1 + retries):
            raw = await self.generate(augmented_system, user_prompt, max_tokens, model_override=model_override)

            # Strip markdown code fences if present
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                lines = lines[1:]  # Remove opening fence
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]  # Remove closing fence
                cleaned = "\n".join(lines)

            # Try parsing as-is first
            try:
                return response_model.model_validate_json(cleaned)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "generate_structured: parse failed (attempt %d/%d), trying repair",
                    attempt + 1, 1 + retries,
                )

            # Try repairing truncated JSON
            try:
                repaired = _repair_truncated_json(cleaned)
                return response_model.model_validate_json(repaired)
            except Exception:
                logger.warning(
                    "generate_structured: repair failed (attempt %d/%d)",
                    attempt + 1, 1 + retries,
                )

            # On retry, add instruction to be more concise
            if attempt < retries:
                augmented_system = (
                    f"{system_prompt}\n\n"
                    f"You MUST respond with valid JSON matching this schema:\n"
                    f"```json\n{schema_json}\n```\n"
                    f"Respond ONLY with the JSON object, no extra text.\n\n"
                    f"IMPORTANT: Your previous response was truncated. "
                    f"Keep file contents concise. Focus on the most critical "
                    f"files first. You MUST complete the JSON — ensure the "
                    f"closing brackets are present."
                )

        raise last_error  # type: ignore[misc]
