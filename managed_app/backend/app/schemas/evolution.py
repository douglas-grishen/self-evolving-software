"""Pydantic schemas for evolution API request/response models."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


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
