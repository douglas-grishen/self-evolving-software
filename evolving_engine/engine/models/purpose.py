"""Purpose model — the guiding specification for all evolution decisions.

Purpose defines what the self-evolving software must achieve and maintain:
functional requirements, technical requirements, security constraints, and
evolution directives. Every evolution cycle consults the Purpose to ensure
changes align with the system's goals.

Purpose is versioned. When an Inception modifies it, the old version is
archived to purpose_history/ and the version number increments.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class PurposeIdentity(BaseModel):
    """The system's identity as defined in the Purpose."""

    name: str
    description: str


class Purpose(BaseModel):
    """The complete specification guiding all evolution decisions."""

    version: int = 1
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    identity: PurposeIdentity
    functional_requirements: list[str] = Field(default_factory=list)
    technical_requirements: list[str] = Field(default_factory=list)
    security_requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    evolution_directives: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Purpose:
        """Load Purpose from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)

    def save(self, path: Path) -> None:
        """Write Purpose to a YAML file."""
        data = self.model_dump(mode="json")
        # Convert datetime to ISO string for clean YAML
        if isinstance(data.get("updated_at"), str):
            pass  # already serialized
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def archive(self, purpose_path: Path, history_dir: Path) -> None:
        """Archive the current purpose file before overwriting with a new version."""
        history_dir.mkdir(parents=True, exist_ok=True)
        archive_name = f"purpose_v{self.version}.yaml"
        shutil.copy2(purpose_path, history_dir / archive_name)

    def to_prompt_context(self) -> str:
        """Format Purpose as a text block suitable for LLM system/user prompts."""
        lines = [
            f"## System Purpose (v{self.version})",
            f"**{self.identity.name}**: {self.identity.description.strip()}",
            "",
            "### Functional Requirements",
        ]
        for req in self.functional_requirements:
            lines.append(f"- {req}")

        lines.append("\n### Technical Requirements")
        for req in self.technical_requirements:
            lines.append(f"- {req}")

        lines.append("\n### Security Requirements")
        for req in self.security_requirements:
            lines.append(f"- {req}")

        lines.append("\n### Constraints")
        for c in self.constraints:
            lines.append(f"- {c}")

        lines.append("\n### Evolution Directives")
        for d in self.evolution_directives:
            lines.append(f"- {d}")

        return "\n".join(lines)

    def to_yaml_string(self) -> str:
        """Serialize Purpose to a YAML string (for storing in DB)."""
        data = self.model_dump(mode="json")
        return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)

    @classmethod
    def from_yaml_string(cls, yaml_str: str) -> Purpose:
        """Deserialize Purpose from a YAML string."""
        data = yaml.safe_load(yaml_str)
        return cls.model_validate(data)

    def diff_summary(self, other: Purpose) -> str:
        """Produce a human-readable summary of differences between two Purpose versions."""
        changes: list[str] = []

        if self.identity != other.identity:
            changes.append("Identity modified")

        for field_name in (
            "functional_requirements",
            "technical_requirements",
            "security_requirements",
            "constraints",
            "evolution_directives",
        ):
            old_set = set(getattr(self, field_name))
            new_set = set(getattr(other, field_name))
            added = new_set - old_set
            removed = old_set - new_set
            label = field_name.replace("_", " ").title()
            for item in added:
                changes.append(f"+ [{label}] {item}")
            for item in removed:
                changes.append(f"- [{label}] {item}")

        return "\n".join(changes) if changes else "No changes"
