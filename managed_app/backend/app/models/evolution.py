"""ORM models for evolution tracking, inceptions, and purpose history.

These tables store the evolution lifecycle events reported by the engine,
inception requests that modify the system's Purpose, and purpose version history.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class EvolutionEventRecord(Base):
    """An evolution lifecycle record — one row per evolution cycle."""

    __tablename__ = "evolution_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    request_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)  # received, completed, failed, etc.
    source: Mapped[str] = mapped_column(String(20))  # user | monitor
    user_request: Mapped[str] = mapped_column(Text)
    plan_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    risk_level: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    validation_passed: Mapped[Optional[bool]] = mapped_column(nullable=True)
    deployment_success: Mapped[Optional[bool]] = mapped_column(nullable=True)
    commit_sha: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    branch: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    events_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_evolution_events_created_at", "created_at"),
    )


class InceptionRecord(Base):
    """An inception request — a directive to modify the system's Purpose."""

    __tablename__ = "inceptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(20))  # human | system | external
    directive: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    previous_purpose_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    new_purpose_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    changes_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_inceptions_submitted_at", "submitted_at"),
    )


class PurposeRecord(Base):
    """A purpose version — stores the full YAML content of each Purpose version."""

    __tablename__ = "purposes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    content_yaml: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    inception_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("inceptions.id"), nullable=True
    )


class EvolutionBacklogItemRecord(Base):
    """A persisted proactive roadmap item that survives across engine runs."""

    __tablename__ = "evolution_backlog_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    purpose_version: Mapped[int] = mapped_column(Integer, index=True)
    task_key: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    priority: Mapped[str] = mapped_column(String(10), default="normal", index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    task_type: Mapped[str] = mapped_column(String(20), default="evolve")
    execution_request: Mapped[str] = mapped_column(Text, default="")
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSONB, default=list)
    depends_on: Mapped[list[str]] = mapped_column(JSONB, default=list)
    app_spec: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="planner")
    last_request_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    blocked_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    retry_after: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ux_evolution_backlog_items_purpose_task_key",
            "purpose_version",
            "task_key",
            unique=True,
        ),
        Index(
            "ix_evolution_backlog_items_purpose_sequence",
            "purpose_version",
            "sequence",
        ),
    )


class SystemNotificationRecord(Base):
    """A persistent operational notification shown to the user until acknowledged."""

    __tablename__ = "system_notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(20), default="system", index=True)
    kind: Mapped[str] = mapped_column(String(40), default="runtime_blocker", index=True)
    severity: Mapped[str] = mapped_column(String(20), default="high", index=True)
    message: Mapped[str] = mapped_column(Text)
    message_hash: Mapped[str] = mapped_column(String(64), index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    update_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )

    __table_args__ = (
        Index("ix_system_notifications_updated_at", "updated_at"),
        Index("ix_system_notifications_hash_updated", "message_hash", "updated_at"),
    )
