"""Models for the persisted proactive backlog."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class BacklogTaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    ABANDONED = "abandoned"


class BacklogTaskPriority(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class BacklogTaskType(str, Enum):
    CREATE_APP = "create_app"
    EVOLVE = "evolve"


class BacklogFeatureSpec(BaseModel):
    name: str
    description: str = ""


class BacklogCapabilitySpec(BaseModel):
    name: str
    description: str = ""
    is_background: bool = False


class BacklogAppSpec(BaseModel):
    name: str
    icon: str = ""
    goal: str = ""
    features: list[BacklogFeatureSpec] = Field(default_factory=list)
    capabilities: list[BacklogCapabilitySpec] = Field(default_factory=list)


class BacklogPlanItem(BaseModel):
    task_key: str
    title: str
    description: str = ""
    status: BacklogTaskStatus = BacklogTaskStatus.PENDING
    priority: BacklogTaskPriority = BacklogTaskPriority.NORMAL
    sequence: int = 0
    task_type: BacklogTaskType = BacklogTaskType.EVOLVE
    execution_request: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    app_spec: BacklogAppSpec | None = None
    source: str = "planner"
    blocked_reason: str | None = None


class BacklogPlannerResponse(BaseModel):
    summary: str = ""
    items: list[BacklogPlanItem] = Field(default_factory=list)


class BacklogItem(BacklogPlanItem):
    id: str
    purpose_version: int
    last_request_id: str | None = None
    attempt_count: int = 0
    failure_streak: int = 0
    last_error: str | None = None
    retry_after: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_attempted_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
