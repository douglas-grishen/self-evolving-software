"""Framework invariants shared by every instance of the open-source platform."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class FrameworkIdentity(BaseModel):
    """Metadata describing the shared framework."""

    name: str
    description: str


class FrameworkInvariants(BaseModel):
    """Non-negotiable platform rules that apply across all instances."""

    version: int = 1
    updated_at: datetime
    identity: FrameworkIdentity
    platform_invariants: list[str] = Field(default_factory=list)
    safety_invariants: list[str] = Field(default_factory=list)
    operator_invariants: list[str] = Field(default_factory=list)
    evolution_invariants: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> FrameworkInvariants:
        """Load framework invariants from YAML."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)

    def to_prompt_context(self) -> str:
        """Format the invariants for LLM prompts."""
        lines = [
            f"## Framework Invariants (v{self.version})",
            f"**{self.identity.name}**: {self.identity.description.strip()}",
            "",
            "### Platform Invariants",
        ]
        for item in self.platform_invariants:
            lines.append(f"- {item}")

        lines.append("\n### Safety Invariants")
        for item in self.safety_invariants:
            lines.append(f"- {item}")

        lines.append("\n### Operator Invariants")
        for item in self.operator_invariants:
            lines.append(f"- {item}")

        lines.append("\n### Evolution Invariants")
        for item in self.evolution_invariants:
            lines.append(f"- {item}")

        return "\n".join(lines)
