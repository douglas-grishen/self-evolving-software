"""Genesis model — captures the initial deployment state of the system.

Genesis is a read-only snapshot created once at the time of first deployment.
It records what was deployed at "time zero" so the engine always knows its
baseline and can reason about how far the system has evolved.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class Genesis(BaseModel):
    """The initial state of the self-evolving software system."""

    version: str
    created_at: datetime
    description: str
    components: dict[str, Any]
    initial_purpose_ref: str
    git_sha: str = ""

    @classmethod
    def load(cls, path: Path) -> Genesis:
        """Load Genesis from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)

    def to_context_string(self) -> str:
        """Format Genesis as a text block for LLM context."""
        lines = [
            f"Genesis v{self.version} (created {self.created_at.isoformat()})",
            f"Description: {self.description.strip()}",
            "Components:",
        ]
        for name, info in self.components.items():
            lines.append(f"  - {name}: {info}")
        if self.git_sha:
            lines.append(f"Initial commit: {self.git_sha}")
        return "\n".join(lines)
