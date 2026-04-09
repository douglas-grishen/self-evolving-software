"""Schemas for runtime skills API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.skills_runtime.models import SkillArtifact


class SkillResponse(BaseModel):
    id: str
    key: str
    name: str
    description: str
    status: str
    scope: str
    executor_kind: str
    config_json: dict[str, Any] = Field(default_factory=dict)
    permissions_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SkillSchemaResponse(BaseModel):
    skill: SkillResponse
    input_schema: dict[str, Any]


class SkillInvocationRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


class SkillInvocationResponse(BaseModel):
    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[SkillArtifact] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    error: str | None = None
