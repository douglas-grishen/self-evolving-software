"""Models for runtime skill inventory fetched from the backend."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AvailableSkill(BaseModel):
    """A runtime skill exposed by the Operational Plane backend."""

    key: str
    name: str
    description: str = ""
    status: str = "active"
    scope: str = "engine_and_apps"
    executor_kind: str = "local"
    config_json: dict[str, Any] = Field(default_factory=dict)
    permissions_json: dict[str, Any] = Field(default_factory=dict)

    def to_prompt_line(self) -> str:
        details: list[str] = [self.status, self.scope, self.executor_kind]
        if self.permissions_json:
            details.append(f"permissions={self.permissions_json}")
        return f"- {self.key}: {self.name} ({', '.join(details)}) — {self.description}"
