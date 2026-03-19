"""EngineMemory — inter-session lessons learned for the evolving engine.

Each record captures a pattern the engine should remember across evolution cycles:
forbidden column names, correct import paths, architectural conventions, etc.

Lessons are injected into the code generator's system prompt at the start of each
cycle, so the engine never repeats the same mistake twice.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class EngineMemory(Base):
    """A single lesson learned by the evolving engine.

    Severity levels:
      critical — repeating this mistake will always cause a failure/crash
      warning  — repeating this is likely harmful but may not always fail
      info     — useful context; shown in UI only, NOT injected into LLM prompts

    Source:
      auto   — extracted by the engine after a failed evolution cycle
      manual — written by a human via the dashboard UI
    """

    __tablename__ = "engine_memory"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    # "forbidden_pattern" | "best_practice" | "bug_fix" | "architecture_note"
    category: Mapped[str] = mapped_column(String(30), nullable=False)

    # Short human-readable title (shown in prompt + UI)
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    # Full lesson text — actionable, 1-3 sentences
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # "auto" (engine-generated) | "manual" (human-written)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="auto")

    # "critical" | "warning" | "info"
    severity: Mapped[str] = mapped_column(String(10), nullable=False)

    # Soft-delete: inactive lessons are excluded from LLM injection
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Incremented when the engine detects the same mistake again
    times_reinforced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_engine_memory_active", "active"),
        Index("ix_engine_memory_severity", "severity"),
        Index("ix_engine_memory_category", "category"),
    )
