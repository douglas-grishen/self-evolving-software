"""Inception models — directives that modify the system's Purpose.

An Inception is not a regular evolution request. It doesn't tell the engine
"do X" — it tells the engine "change what you're trying to achieve." It
modifies the Purpose itself, altering the direction of all future evolution.

Inceptions can come from:
  - HUMAN: A person submitting a directive via the UI
  - SYSTEM: An internal system detecting a need to change direction
  - EXTERNAL: An external service or API pushing a directive
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class InceptionSource(str, Enum):
    """Who or what originated the inception."""

    HUMAN = "human"
    SYSTEM = "system"
    EXTERNAL = "external"


class InceptionStatus(str, Enum):
    """Processing status of an inception."""

    PENDING = "pending"
    PROCESSING = "processing"
    APPLIED = "applied"
    REJECTED = "rejected"


class InceptionRequest(BaseModel):
    """An inception directive received from the backend API."""

    id: str
    source: InceptionSource = InceptionSource.HUMAN
    directive: str
    rationale: str = ""
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: InceptionStatus = InceptionStatus.PENDING


class InceptionResult(BaseModel):
    """The outcome of processing an inception through the PurposeEvolver."""

    inception_id: str
    previous_purpose_version: int
    new_purpose_version: int
    changes_summary: str
    applied_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
