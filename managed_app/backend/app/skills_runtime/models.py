"""Shared models for executable runtime skills."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillArtifact(BaseModel):
    """Structured artifact emitted by a skill invocation."""

    name: str
    kind: str
    content_type: str
    data: str
    encoding: str | None = None


class SkillMetadata(BaseModel):
    """Static/runtime metadata describing a skill."""

    key: str
    name: str
    description: str = ""
    status: str = "active"
    scope: str = "engine_and_apps"
    executor_kind: str = "local"
    config_json: dict[str, Any] = Field(default_factory=dict)
    permissions_json: dict[str, Any] = Field(default_factory=dict)


class SkillInvocationRequest(BaseModel):
    """Generic invocation contract for all runtime skills."""

    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


class SkillInvocationResponse(BaseModel):
    """Normalized response contract for all runtime skills."""

    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[SkillArtifact] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    error: str | None = None


class SkillExecutionContext(BaseModel):
    """Resolved execution context for one concrete skill invocation."""

    skill: SkillMetadata
    settings: dict[str, str] = Field(default_factory=dict)
    request_context: dict[str, Any] = Field(default_factory=dict)
