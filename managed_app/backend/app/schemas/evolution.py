"""Pydantic schemas for evolution API request/response models."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Evolution Events
# ---------------------------------------------------------------------------


class EvolutionEventCreate(BaseModel):
    """Payload the engine sends when an evolution starts or updates."""

    request_id: str
    status: str
    source: str = "user"
    user_request: str = ""
    plan_summary: Optional[str] = None
    risk_level: Optional[str] = None
    validation_passed: Optional[bool] = None
    deployment_success: Optional[bool] = None
    commit_sha: Optional[str] = None
    branch: Optional[str] = None
    error: Optional[str] = None
    completed_at: Optional[datetime] = None
    events_json: Optional[Dict[str, Any]] = None


class EvolutionEventResponse(BaseModel):
    """An evolution event as returned by the API."""

    id: str
    request_id: str
    status: str
    source: str
    user_request: str
    plan_summary: Optional[str] = None
    risk_level: Optional[str] = None
    validation_passed: Optional[bool] = None
    deployment_success: Optional[bool] = None
    commit_sha: Optional[str] = None
    branch: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    events_json: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Inceptions
# ---------------------------------------------------------------------------


class InceptionCreate(BaseModel):
    """Payload for submitting a new inception (from human or external system)."""

    source: str = "human"  # human | system | external
    directive: str
    rationale: str = ""


class InceptionUpdate(BaseModel):
    """Payload the engine sends when it processes an inception."""

    status: str  # applied | rejected | processing
    processed_at: Optional[datetime] = None
    previous_purpose_version: Optional[int] = None
    new_purpose_version: Optional[int] = None
    changes_summary: Optional[str] = None


class InceptionResponse(BaseModel):
    """An inception as returned by the API."""

    id: str
    source: str
    directive: str
    rationale: str
    status: str
    submitted_at: datetime
    processed_at: Optional[datetime] = None
    previous_purpose_version: Optional[int] = None
    new_purpose_version: Optional[int] = None
    changes_summary: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Purpose
# ---------------------------------------------------------------------------


class PurposeCreate(BaseModel):
    """Payload the engine sends to store a purpose version."""

    version: int
    content_yaml: str
    inception_id: Optional[str] = None


class PurposeResponse(BaseModel):
    """A purpose version as returned by the API."""

    id: str
    version: int
    content_yaml: str
    created_at: datetime
    inception_id: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Backlog
# ---------------------------------------------------------------------------


class BacklogAppFeatureSpec(BaseModel):
    name: str
    description: str = ""


class BacklogAppCapabilitySpec(BaseModel):
    name: str
    description: str = ""
    is_background: bool = False


class BacklogAppSpec(BaseModel):
    name: str
    icon: str = ""
    goal: str = ""
    features: List[BacklogAppFeatureSpec] = Field(default_factory=list)
    capabilities: List[BacklogAppCapabilitySpec] = Field(default_factory=list)


class BacklogItemBase(BaseModel):
    task_key: str
    title: str
    description: str = ""
    status: str = "pending"
    priority: str = "normal"
    sequence: int = 0
    task_type: str = "evolve"
    execution_request: str = ""
    acceptance_criteria: List[str] = Field(default_factory=list)
    depends_on: List[str] = Field(default_factory=list)
    app_spec: Optional[BacklogAppSpec] = None
    source: str = "planner"
    blocked_reason: Optional[str] = None


class BacklogItemSync(BacklogItemBase):
    pass


class BacklogSyncRequest(BaseModel):
    purpose_version: int
    items: List[BacklogItemSync]


class BacklogItemUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    sequence: Optional[int] = None
    task_type: Optional[str] = None
    execution_request: Optional[str] = None
    acceptance_criteria: Optional[List[str]] = None
    depends_on: Optional[List[str]] = None
    app_spec: Optional[BacklogAppSpec] = None
    source: Optional[str] = None
    last_request_id: Optional[str] = None
    attempt_count: Optional[int] = None
    failure_streak: Optional[int] = None
    last_error: Optional[str] = None
    blocked_reason: Optional[str] = None
    retry_after: Optional[datetime] = None
    last_attempted_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class BacklogItemResponse(BacklogItemBase):
    id: str
    purpose_version: int
    last_request_id: Optional[str] = None
    attempt_count: int = 0
    failure_streak: int = 0
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    retry_after: Optional[datetime] = None
    last_attempted_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class DashboardStatusResponse(BaseModel):
    """Aggregated dashboard status for the Evolution Monitor UI."""

    total_evolutions: int = 0
    active_evolutions: int = 0
    completed_evolutions: int = 0
    failed_evolutions: int = 0
    current_purpose_version: Optional[int] = None
    pending_inceptions: int = 0
    last_evolution: Optional[EvolutionEventResponse] = None
