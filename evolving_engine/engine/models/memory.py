"""Engine-side representation of an EngineMemory lesson.

This is the engine's local Pydantic view of a lesson fetched from the backend API.
It has no SQLAlchemy dependency — the engine communicates with the DB exclusively
through the backend HTTP API.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class EngineMemory(BaseModel):
    """A single lesson learned — fetched from GET /api/v1/memory."""

    id: str
    category: str       # "forbidden_pattern" | "best_practice" | "bug_fix" | "architecture_note"
    title: str
    content: str
    source: str         # "auto" | "manual"
    severity: str       # "critical" | "warning" | "info"
    active: bool
    times_reinforced: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_api_dict(cls, data: dict) -> "EngineMemory":
        """Construct from a raw API response dict."""
        return cls(**data)
