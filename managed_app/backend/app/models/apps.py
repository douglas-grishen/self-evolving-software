"""ORM models for the Apps, Features, and Capabilities framework.

Conceptual model:
  - Feature:    A user-facing behavior that a person can perceive, access, or use.
  - Capability: An internal system ability that enables or supports features.
                A capability can support one or more features, or exist independently.
  - App:        A cohesive collection of Features and Capabilities with a concrete goal.
                Each App is displayed as a desktop icon and can be launched by the user.

Relationships:
  - Feature → Capability: many-to-many (a feature can require multiple capabilities)
  - App → Feature: one-to-many (an app owns its features)
  - App → Capability: many-to-many (an app can have standalone capabilities)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Table, Text, Column, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Junction tables (many-to-many)
# ---------------------------------------------------------------------------

feature_capabilities = Table(
    "feature_capabilities",
    Base.metadata,
    Column("feature_id", String(36), ForeignKey("features.id", ondelete="CASCADE"), primary_key=True),
    Column("capability_id", String(36), ForeignKey("capabilities.id", ondelete="CASCADE"), primary_key=True),
)

app_capabilities = Table(
    "app_capabilities",
    Base.metadata,
    Column("app_id", String(36), ForeignKey("apps.id", ondelete="CASCADE"), primary_key=True),
    Column("capability_id", String(36), ForeignKey("capabilities.id", ondelete="CASCADE"), primary_key=True),
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AppRecord(Base):
    """An App — a cohesive collection of Features and Capabilities.

    Displayed as a desktop icon. Users can launch it by clicking the icon.
    """

    __tablename__ = "apps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str] = mapped_column(String(10), default="")  # emoji icon
    goal: Mapped[str] = mapped_column(Text, default="")  # concrete objective
    status: Mapped[str] = mapped_column(String(20), default="planned")  # planned | building | active | archived
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    created_by_evolution_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Relationships
    features: Mapped[List["FeatureRecord"]] = relationship(
        "FeatureRecord", back_populates="app", cascade="all, delete-orphan",
        lazy="selectin",
    )
    capabilities: Mapped[List["CapabilityRecord"]] = relationship(
        "CapabilityRecord", secondary=app_capabilities, back_populates="apps",
        lazy="selectin",
    )


class FeatureRecord(Base):
    """A Feature — a user-facing behavior or element.

    Something a person can perceive, access, or use directly.
    A feature belongs to an App and depends on one or more Capabilities.
    """

    __tablename__ = "features"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    app_id: Mapped[str] = mapped_column(String(36), ForeignKey("apps.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")  # internal description
    user_facing_description: Mapped[str] = mapped_column(Text, default="")  # what the user sees/experiences
    status: Mapped[str] = mapped_column(String(20), default="planned")  # planned | building | implemented | verified
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Relationships
    app: Mapped["AppRecord"] = relationship("AppRecord", back_populates="features")
    capabilities: Mapped[List["CapabilityRecord"]] = relationship(
        "CapabilityRecord", secondary=feature_capabilities, back_populates="features",
        lazy="selectin",
    )


class CapabilityRecord(Base):
    """A Capability — an internal system ability.

    Enables or supports features, or exists independently for background
    processing, inter-system communication, orchestration, etc.
    """

    __tablename__ = "capabilities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="planned")  # planned | building | implemented | verified
    is_background: Mapped[bool] = mapped_column(Boolean, default=False)  # runs entirely in background
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Relationships (back-references)
    features: Mapped[List["FeatureRecord"]] = relationship(
        "FeatureRecord", secondary=feature_capabilities, back_populates="capabilities",
        lazy="selectin",
    )
    apps: Mapped[List["AppRecord"]] = relationship(
        "AppRecord", secondary=app_capabilities, back_populates="capabilities",
        lazy="selectin",
    )
