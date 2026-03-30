"""Data models for the Monitor phase of the MAPE-K loop.

A RuntimeSnapshot is a point-in-time observation of the Operational Plane's
health, performance, and error state. Anomalies are deviations from expected
behavior that the Analyze phase will interpret to decide whether the system
needs to evolve.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AnomalyType(str, Enum):
    """Categories of runtime anomalies the engine can detect."""

    HIGH_ERROR_RATE = "high_error_rate"          # error_rate > threshold
    HIGH_LATENCY = "high_latency"                # avg latency degraded
    SERVICE_UNREACHABLE = "service_unreachable"  # backend does not respond
    DATABASE_DEGRADED = "database_degraded"      # DB latency spike or failure
    REPEATED_EXCEPTION = "repeated_exception"    # same exception recurs
    MISSING_ENDPOINT = "missing_endpoint"        # expected route returns 404
    SCHEMA_DRIFT = "schema_drift"                # DB schema changed unexpectedly


class Anomaly(BaseModel):
    """A single detected deviation from expected runtime behavior."""

    type: AnomalyType
    severity: str = "medium"        # "low" | "medium" | "high" | "critical"
    description: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    suggested_action: str = ""      # hint for the Analyze / Plan phases


class EndpointMetrics(BaseModel):
    """Performance snapshot for a single API endpoint."""

    method: str
    path: str
    request_count: int
    avg_latency_ms: float
    error_rate: float
    error_count: int


class ContractProbeFailure(BaseModel):
    """A mounted app contract probe that returned the wrong result."""

    app_key: str
    method: str
    path: str
    description: str
    expected_statuses: list[int] = Field(default_factory=list)
    status_code: int | None = None
    detail: str = ""


class HealthCheck(BaseModel):
    """Result of the detailed health probe against the Operational Plane."""

    status: str                  # "ok" | "degraded"
    checks: dict[str, str]
    db_latency_ms: float | None
    app_version: str
    environment: str
    timestamp: datetime


class DatabaseSchema(BaseModel):
    """Current database schema as reported by the Operational Plane."""

    tables: list[dict[str, Any]] = Field(default_factory=list)
    table_count: int = 0
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class RuntimeSnapshot(BaseModel):
    """Complete point-in-time observation of the Operational Plane.

    Produced by RuntimeObserver and consumed by the Analyze phase to decide
    whether and what kind of evolution is needed.
    """

    observed_at: datetime = Field(default_factory=datetime.utcnow)
    reachable: bool = True

    # Health & metrics from /api/v1/monitor/*
    health: HealthCheck | None = None
    total_requests: int = 0
    total_errors: int = 0
    global_error_rate: float = 0.0
    uptime_seconds: float = 0.0
    endpoints: list[EndpointMetrics] = Field(default_factory=list)
    recent_errors: list[dict[str, Any]] = Field(default_factory=list)
    contract_failures: list[ContractProbeFailure] = Field(default_factory=list)

    # Database state
    schema: DatabaseSchema | None = None

    # Docker container states (from Docker socket)
    container_states: dict[str, str] = Field(default_factory=dict)  # name → status

    # Derived anomalies — populated by RuntimeObserver after collection
    anomalies: list[Anomaly] = Field(default_factory=list)

    @property
    def has_anomalies(self) -> bool:
        return len(self.anomalies) > 0

    @property
    def critical_anomalies(self) -> list[Anomaly]:
        return [a for a in self.anomalies if a.severity == "critical"]

    def summary(self) -> str:
        """One-line summary for logging."""
        if not self.reachable:
            return "UNREACHABLE"
        parts = [
            f"requests={self.total_requests}",
            f"error_rate={self.global_error_rate:.1%}",
            f"uptime={self.uptime_seconds:.0f}s",
            f"anomalies={len(self.anomalies)}",
        ]
        return " | ".join(parts)
