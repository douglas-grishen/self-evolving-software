"""Base abstractions and errors for runtime skills."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from app.skills_runtime.models import (
    SkillExecutionContext,
    SkillInvocationRequest,
    SkillInvocationResponse,
    SkillMetadata,
)


class SkillRuntimeError(RuntimeError):
    """Base runtime error for skill resolution and execution."""


class SkillNotFoundError(SkillRuntimeError):
    """Raised when the requested skill is not registered."""


class SkillDisabledError(SkillRuntimeError):
    """Raised when a skill exists but is not executable."""


class SkillValidationError(SkillRuntimeError):
    """Raised when invocation input does not satisfy the skill contract."""


class BaseSkill(ABC):
    """Abstract executable runtime skill."""

    input_model: type[BaseModel]

    @abstractmethod
    def metadata(self) -> SkillMetadata:
        """Return the runtime metadata for the skill."""

    def input_schema(self) -> dict[str, Any]:
        """Return the JSON schema used to validate invocation input."""
        return self.input_model.model_json_schema()

    def validate_input(self, payload: dict[str, Any]) -> BaseModel:
        """Validate raw input and return the typed skill-specific model."""
        try:
            return self.input_model.model_validate(payload)
        except Exception as exc:
            raise SkillValidationError(str(exc)) from exc

    @abstractmethod
    async def invoke(
        self,
        request: SkillInvocationRequest,
        context: SkillExecutionContext,
    ) -> SkillInvocationResponse:
        """Execute the skill and return a normalized response."""
