"""Pydantic schemas for the engine_memory API."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class MemoryCreate(BaseModel):
    """Payload for creating a new lesson (used by the engine and the UI)."""

    category: str   # "forbidden_pattern" | "best_practice" | "bug_fix" | "architecture_note"
    title: str
    content: str
    source: str = "auto"    # "auto" | "manual"
    severity: str           # "critical" | "warning" | "info"


class MemoryPatch(BaseModel):
    """Partial update — all fields optional."""

    title: Optional[str] = None
    content: Optional[str] = None
    severity: Optional[str] = None
    active: Optional[bool] = None
    times_reinforced: Optional[int] = None  # engine can increment this


class MemoryResponse(BaseModel):
    """Full lesson representation returned by the API."""

    id: str
    created_at: datetime
    updated_at: datetime
    category: str
    title: str
    content: str
    source: str
    severity: str
    active: bool
    times_reinforced: int

    model_config = {"from_attributes": True}
