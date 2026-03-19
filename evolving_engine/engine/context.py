"""EvolutionContext — the shared state object that flows through the agent pipeline.

Each agent receives the context, performs its work, and returns an updated copy.
The context is immutable (Pydantic model) to ensure clean state transitions.

The context now carries full provenance — whether the evolution was triggered
by a user request or by the runtime monitor detecting an anomaly — so agents
can adapt their reasoning accordingly.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from engine.models.evolution import (
    DeploymentResult,
    EvolutionEvent,
    EvolutionPlan,
    EvolutionRequest,
    EvolutionSource,
    EvolutionStatus,
    EvolutionTarget,
    GeneratedFile,
    ValidationResult,
)
from engine.models.memory import EngineMemory
from engine.models.repo_map import RepoMap


class EvolutionContext(BaseModel):
    """Immutable state object passed through the evolution pipeline.

    Each agent reads what it needs, performs its work, and returns a new
    context with updated fields. The history list provides a full audit trail.
    """

    # Identity
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Input — what triggered this evolution and what it should change
    request: EvolutionRequest

    # Runtime snapshot attached by monitor-triggered evolutions
    # Contains health, metrics, errors, schema observed at trigger time
    runtime_snapshot: dict[str, Any] = Field(default_factory=dict)

    # Pipeline state
    status: EvolutionStatus = EvolutionStatus.RECEIVED

    # Produced by Data Manager Agent
    repo_map: RepoMap | None = None

    # Lessons fetched from backend at the start of each cycle (by DataManagerAgent).
    # Injected into the code generator's system prompt to prevent repeated mistakes.
    lessons: list[EngineMemory] = Field(default_factory=list)

    # Produced by Leader Agent
    plan: EvolutionPlan | None = None

    # Produced by Code Generator Agent
    generated_files: list[GeneratedFile] = Field(default_factory=list)

    # Produced by Code Validator Agent
    validation_result: ValidationResult | None = None

    # Produced by Deployer
    deployment_result: DeploymentResult | None = None

    # Error handling
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 3

    # Audit trail
    history: list[EvolutionEvent] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # State transitions (return new context — immutable pattern)
    # ------------------------------------------------------------------

    def add_event(
        self, agent: str, action: str, status: str, details: str = ""
    ) -> "EvolutionContext":
        """Return a new context with an appended audit event."""
        event = EvolutionEvent(agent=agent, action=action, status=status, details=details)
        return self.model_copy(update={"history": [*self.history, event]})

    def transition(self, new_status: EvolutionStatus) -> "EvolutionContext":
        """Return a new context with an updated pipeline status."""
        return self.model_copy(update={"status": new_status})

    def fail(self, error_message: str) -> "EvolutionContext":
        """Return a new context marked as FAILED with an error message."""
        return self.model_copy(
            update={
                "status": EvolutionStatus.FAILED,
                "error": error_message,
            }
        )

    @property
    def can_retry(self) -> bool:
        """Check if the pipeline has retries remaining."""
        return self.retry_count < self.max_retries

    def increment_retry(self) -> "EvolutionContext":
        """Return a new context with an incremented retry counter."""
        return self.model_copy(update={"retry_count": self.retry_count + 1})

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def is_monitor_triggered(self) -> bool:
        """True if this evolution was triggered by the runtime monitor."""
        return self.request.source == EvolutionSource.MONITOR

    @property
    def target(self) -> EvolutionTarget:
        """The codebase this evolution is targeting."""
        return self.request.target


def create_context(
    user_request: str,
    dry_run: bool = False,
    source: EvolutionSource = EvolutionSource.USER,
    runtime_snapshot: Any | None = None,
    target: EvolutionTarget = EvolutionTarget.AUTO,
) -> EvolutionContext:
    """Factory: create a new EvolutionContext.

    Args:
        user_request:     Natural language description of the desired change.
        dry_run:          If True, validate but skip deployment.
        source:           Who/what triggered this evolution.
        runtime_snapshot: RuntimeSnapshot from the monitor (for anomaly-driven runs).
        target:           Which codebase to evolve (or AUTO to let the Leader decide).
    """
    snapshot_dict: dict[str, Any] = {}
    if runtime_snapshot is not None:
        # Store a serialisable subset of the snapshot (anomalies + metrics)
        try:
            snapshot_dict = runtime_snapshot.model_dump(
                include={
                    "observed_at", "reachable", "global_error_rate",
                    "total_requests", "total_errors", "anomalies",
                    "recent_errors", "container_states",
                }
            )
        except Exception:
            pass

    return EvolutionContext(
        request=EvolutionRequest(
            user_request=user_request,
            dry_run=dry_run,
            source=source,
            target=target,
        ),
        runtime_snapshot=snapshot_dict,
    )
