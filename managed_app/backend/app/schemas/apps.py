"""Pydantic schemas for the Apps, Features, and Capabilities API."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------


class CapabilityCreate(BaseModel):
    name: str
    description: str = ""
    is_background: bool = False


class CapabilityResponse(BaseModel):
    id: str
    name: str
    description: str
    status: str
    is_background: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class CapabilityBrief(BaseModel):
    """Short version for embedding in Feature/App responses."""
    id: str
    name: str
    status: str
    is_background: bool

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Feature
# ---------------------------------------------------------------------------


class FeatureCreate(BaseModel):
    name: str
    description: str = ""
    user_facing_description: str = ""
    capability_ids: List[str] = []  # link to existing capabilities


class FeatureResponse(BaseModel):
    id: str
    app_id: str
    name: str
    description: str
    user_facing_description: str
    status: str
    created_at: datetime
    capabilities: List[CapabilityBrief] = []

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


class AppCreate(BaseModel):
    """Create a new App (used by both admin and engine)."""
    name: str
    description: str = ""
    icon: str = ""  # emoji
    goal: str = ""
    status: str = "planned"
    features: List[FeatureCreate] = []
    capability_ids: List[str] = []  # standalone capabilities
    metadata_json: Optional[Dict[str, Any]] = None


class AppUpdate(BaseModel):
    """Partial update for an App."""
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    goal: Optional[str] = None
    status: Optional[str] = None
    metadata_json: Optional[Dict[str, Any]] = None


class AppResponse(BaseModel):
    """Full App with features and capabilities."""
    id: str
    name: str
    description: str
    icon: str
    goal: str
    status: str
    created_at: datetime
    updated_at: datetime
    created_by_evolution_id: Optional[str] = None
    features: List[FeatureResponse] = []
    capabilities: List[CapabilityBrief] = []
    metadata_json: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}


class AppBrief(BaseModel):
    """Short version for desktop icon listing."""
    id: str
    name: str
    icon: str
    status: str
    goal: str
    feature_count: int = 0
    capability_count: int = 0

    model_config = {"from_attributes": True}
