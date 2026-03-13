"""Models for the evolution pipeline: requests, plans, results, and audit events."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from engine.monitor.models import RuntimeSnapshot


class EvolutionStatus(str, Enum):
    """States in the evolution pipeline state machine."""

    RECEIVED = "received"
    ANALYZING = "analyzing"
    GENERATING = "generating"
    VALIDATING = "validating"
    DEPLOYING = "deploying"
    COMPLETED = "completed"
    FAILED = "failed"


class EvolutionSource(str, Enum):
    """What initiated this evolution cycle."""

    USER = "user"       # Triggered by an explicit user request
    MONITOR = "monitor" # Triggered autonomously by the runtime observer


class EvolutionTarget(str, Enum):
    """Which codebase the engine will modify in this cycle."""

    MANAGED_SYSTEM = "managed_system"       # managed_app/ — the web application
    AUTONOMIC_MANAGER = "autonomic_manager" # evolving_engine/ — the engine itself
    AUTO = "auto"                           # Leader agent decides


class EvolutionRequest(BaseModel):
    """A request to evolve a codebase — from a user or from the monitor."""

    user_request: str
    priority: str = "normal"            # "low" | "normal" | "high" | "critical"
    dry_run: bool = False               # If True, validate but skip deployment
    source: EvolutionSource = EvolutionSource.USER
    target: EvolutionTarget = EvolutionTarget.AUTO

    # Attached runtime evidence for monitor-triggered evolutions
    runtime_evidence: dict[str, Any] = Field(default_factory=dict)


class FileChange(BaseModel):
    """A single file change within an evolution plan."""

    file_path: str
    action: str  # "create" | "modify" | "delete"
    description: str
    layer: str  # "frontend" | "backend" | "database" | "config"


class EvolutionPlan(BaseModel):
    """A structured plan produced by the Leader Agent."""

    summary: str
    changes: list[FileChange]
    requires_migration: bool = False
    requires_new_dependencies: bool = False
    risk_level: str = "low"  # "low" | "medium" | "high"
    reasoning: str = ""


class GeneratedFile(BaseModel):
    """A file produced by the Code Generator Agent."""

    file_path: str
    content: str
    action: str  # "create" | "modify" | "delete"
    layer: str  # "frontend" | "backend" | "database"


class ValidationResult(BaseModel):
    """Result from the Code Validator Agent sandbox execution."""

    passed: bool
    risk_score: float = 0.0  # 0.0 (safe) to 1.0 (dangerous)
    static_analysis_passed: bool = True
    build_passed: bool = True
    tests_passed: bool = True
    logs: str = ""
    errors: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class DeploymentResult(BaseModel):
    """Result from the deployment phase."""

    success: bool
    commit_sha: str = ""
    branch: str = ""
    pipeline_execution_id: str = ""
    message: str = ""


class EvolutionEvent(BaseModel):
    """An audit log entry for a single step in the evolution pipeline."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent: str
    action: str
    status: str  # "started" | "completed" | "failed"
    details: str = ""
    duration_ms: int | None = None
