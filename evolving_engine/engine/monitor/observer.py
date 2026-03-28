"""RuntimeObserver — the engine's sensor layer.

Polls the Operational Plane via the control-plane network at a configurable
interval and produces RuntimeSnapshot objects that feed the MAPE-K loop.

Also reads Docker container states directly from the Docker socket so the
engine can detect crashed or restarting services even when the HTTP layer
is unresponsive.

Thresholds for anomaly detection are intentionally conservative defaults
that can be overridden via EngineSettings.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import httpx

from engine.monitor.models import (
    Anomaly,
    AnomalyType,
    ContractProbeFailure,
    DatabaseSchema,
    EndpointMetrics,
    HealthCheck,
    RuntimeSnapshot,
)
from engine.runtime_contracts import (
    get_core_framework_probes,
    get_runtime_contract_probes,
    validate_runtime_contract_response,
)

logger = logging.getLogger(__name__)
_OPERATIONAL_SUBSYSTEM_LABELS = {"operational-plane", "managed-system"}

# ---------------------------------------------------------------------------
# Anomaly detection thresholds (defaults — override via EngineSettings)
# ---------------------------------------------------------------------------
DEFAULT_ERROR_RATE_THRESHOLD = 0.05    # 5%
DEFAULT_LATENCY_THRESHOLD_MS = 800.0   # 800 ms avg
DEFAULT_DB_LATENCY_THRESHOLD_MS = 200.0


class RuntimeObserver:
    """Polls the Operational Plane and converts raw data into RuntimeSnapshot.

    Usage:
        observer = RuntimeObserver(base_url="http://backend:8000")
        snapshot = await observer.observe()
        if snapshot.has_anomalies:
            ...
    """

    def __init__(
        self,
        base_url: str,
        operational_plane_path: Path | None = None,
        managed_app_path: Path | None = None,
        runtime_contracts_path: Path | None = None,
        timeout_seconds: float = 10.0,
        error_rate_threshold: float = DEFAULT_ERROR_RATE_THRESHOLD,
        latency_threshold_ms: float = DEFAULT_LATENCY_THRESHOLD_MS,
        db_latency_threshold_ms: float = DEFAULT_DB_LATENCY_THRESHOLD_MS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._operational_plane_path = operational_plane_path or managed_app_path
        self._runtime_contracts_path = runtime_contracts_path
        self._timeout = timeout_seconds
        self._error_rate_threshold = error_rate_threshold
        self._latency_threshold_ms = latency_threshold_ms
        self._db_latency_threshold_ms = db_latency_threshold_ms
        self._previous_schema: DatabaseSchema | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def observe(self) -> RuntimeSnapshot:
        """Collect a full runtime snapshot and run anomaly detection."""
        snapshot = RuntimeSnapshot(observed_at=datetime.utcnow())

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout
            ) as client:
                # Run all probes concurrently
                health, metrics, errors, schema, contract_failures = await asyncio.gather(
                    self._probe_health(client),
                    self._probe_metrics(client),
                    self._probe_errors(client),
                    self._probe_schema(client),
                    self._probe_runtime_contracts(client),
                    return_exceptions=True,
                )

            snapshot.reachable = True

            if isinstance(health, HealthCheck):
                snapshot.health = health
            if isinstance(metrics, dict):
                snapshot.total_requests = metrics.get("total_requests", 0)
                snapshot.total_errors = metrics.get("total_errors", 0)
                snapshot.global_error_rate = metrics.get("global_error_rate", 0.0)
                snapshot.uptime_seconds = metrics.get("uptime_seconds", 0.0)
                snapshot.endpoints = [
                    EndpointMetrics(**ep) for ep in metrics.get("endpoints", [])
                ]
            if isinstance(errors, list):
                snapshot.recent_errors = errors
            if isinstance(schema, DatabaseSchema):
                snapshot.schema = schema
            if isinstance(contract_failures, list):
                snapshot.contract_failures = contract_failures

            # Try to read Docker container states (best-effort)
            snapshot.container_states = await self._probe_docker_states()

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            snapshot.reachable = False
            snapshot.anomalies.append(
                Anomaly(
                    type=AnomalyType.SERVICE_UNREACHABLE,
                    severity="critical",
                    description=f"Cannot reach Operational Plane at {self._base_url}",
                    evidence={"error": str(exc)},
                    suggested_action="Check that the backend container is running and healthy.",
                )
            )
            logger.error("Operational Plane unreachable: %s", exc)
            return snapshot

        # Run anomaly detection on the collected data
        self._detect_anomalies(snapshot)

        logger.info("Observation complete: %s", snapshot.summary())
        return snapshot

    # ------------------------------------------------------------------
    # Probes — each calls one /api/v1/monitor/* endpoint
    # ------------------------------------------------------------------

    async def _probe_health(self, client: httpx.AsyncClient) -> HealthCheck | Exception:
        try:
            r = await client.get("/api/v1/monitor/health")
            r.raise_for_status()
            data = r.json()
            return HealthCheck(
                status=data["status"],
                checks=data["checks"],
                db_latency_ms=data.get("db_latency_ms"),
                app_version=data.get("app_version", "unknown"),
                environment=data.get("environment", "unknown"),
                timestamp=datetime.fromisoformat(data["timestamp"]),
            )
        except Exception as exc:
            logger.warning("Health probe failed: %s", exc)
            return exc

    async def _probe_metrics(self, client: httpx.AsyncClient) -> dict | Exception:
        try:
            r = await client.get("/api/v1/monitor/metrics")
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.warning("Metrics probe failed: %s", exc)
            return exc

    async def _probe_errors(self, client: httpx.AsyncClient) -> list | Exception:
        try:
            r = await client.get("/api/v1/monitor/errors")
            r.raise_for_status()
            return r.json().get("errors", [])
        except Exception as exc:
            logger.warning("Errors probe failed: %s", exc)
            return exc

    async def _probe_schema(self, client: httpx.AsyncClient) -> DatabaseSchema | Exception:
        try:
            r = await client.get("/api/v1/monitor/schema")
            r.raise_for_status()
            data = r.json()
            schema = DatabaseSchema(
                tables=data.get("tables", []),
                table_count=data.get("table_count", 0),
            )
            return schema
        except Exception as exc:
            logger.warning("Schema probe failed: %s", exc)
            return exc

    async def _probe_runtime_contracts(
        self, client: httpx.AsyncClient
    ) -> list[ContractProbeFailure] | Exception:
        """Actively probe framework and mounted-app contracts outside aggregate error thresholds."""
        try:
            probes = list(get_core_framework_probes())
            if self._operational_plane_path is not None:
                probes.extend(
                    get_runtime_contract_probes(
                        self._operational_plane_path,
                        self._runtime_contracts_path,
                    )
                )
            failures: list[ContractProbeFailure] = []
            for probe in probes:
                try:
                    response = await client.request(
                        probe.method,
                        probe.path,
                        json=probe.json_body,
                    )
                    contract_error = validate_runtime_contract_response(probe, response)
                    if contract_error is None:
                        continue
                    failures.append(
                        ContractProbeFailure(
                            app_key=probe.app_key,
                            method=probe.method,
                            path=probe.path,
                            description=probe.description,
                            expected_statuses=list(probe.expected_statuses),
                            status_code=response.status_code,
                            detail=contract_error,
                        )
                    )
                except Exception as exc:
                    failures.append(
                        ContractProbeFailure(
                            app_key=probe.app_key,
                            method=probe.method,
                            path=probe.path,
                            description=probe.description,
                            expected_statuses=list(probe.expected_statuses),
                            detail=str(exc),
                        )
                    )
            return failures
        except Exception as exc:
            logger.warning("Runtime contract probe failed: %s", exc)
            return exc

    async def _probe_docker_states(self) -> dict[str, str]:
        """Read Operational Plane container states via Docker socket (best-effort)."""
        try:
            import docker  # type: ignore

            client = docker.from_env()
            states: dict[str, str] = {}
            for container in client.containers.list(all=True):
                labels = container.labels or {}
                if labels.get("com.ses.subsystem") in _OPERATIONAL_SUBSYSTEM_LABELS:
                    states[container.name] = container.status
            return states
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Anomaly detection
    # ------------------------------------------------------------------

    def _detect_anomalies(self, snapshot: RuntimeSnapshot) -> None:
        """Inspect a snapshot and append detected Anomaly objects."""

        # 1. Health status
        if snapshot.health and snapshot.health.status != "ok":
            snapshot.anomalies.append(
                Anomaly(
                    type=AnomalyType.DATABASE_DEGRADED,
                    severity="high",
                    description="Operational Plane reports degraded health status.",
                    evidence={"checks": snapshot.health.checks},
                    suggested_action="Inspect database connectivity and migration state.",
                )
            )

        # 2. Database latency
        if (
            snapshot.health
            and snapshot.health.db_latency_ms is not None
            and snapshot.health.db_latency_ms > self._db_latency_threshold_ms
        ):
            snapshot.anomalies.append(
                Anomaly(
                    type=AnomalyType.DATABASE_DEGRADED,
                    severity="medium",
                    description=(
                        f"Database latency is {snapshot.health.db_latency_ms:.0f}ms "
                        f"(threshold: {self._db_latency_threshold_ms:.0f}ms)."
                    ),
                    evidence={"db_latency_ms": snapshot.health.db_latency_ms},
                    suggested_action="Review slow queries or missing indexes.",
                )
            )

        # 3. Global error rate
        for failure in snapshot.contract_failures:
            status = (
                f"HTTP {failure.status_code}"
                if failure.status_code is not None
                else failure.detail
            )
            snapshot.anomalies.append(
                Anomaly(
                    type=AnomalyType.MISSING_ENDPOINT,
                    severity="high",
                    description=(
                        f"Runtime contract probe failed for {failure.method} "
                        f"{failure.path}: {status}"
                    ),
                    evidence=failure.model_dump(),
                    suggested_action=(
                        "Restore the expected backend contract so the desktop can "
                        "recover with a valid empty state instead of a broken request."
                    ),
                )
            )

        # 4. Global error rate
        if (
            snapshot.total_requests > 10
            and snapshot.global_error_rate > self._error_rate_threshold
        ):
            snapshot.anomalies.append(
                Anomaly(
                    type=AnomalyType.HIGH_ERROR_RATE,
                    severity="high",
                    description=(
                        f"Global error rate is {snapshot.global_error_rate:.1%} "
                        f"(threshold: {self._error_rate_threshold:.1%})."
                    ),
                    evidence={
                        "error_rate": snapshot.global_error_rate,
                        "total_errors": snapshot.total_errors,
                        "recent_errors": snapshot.recent_errors[:5],
                    },
                    suggested_action=(
                        "Inspect recent error logs. The Generator agent should be given "
                        "these errors as context when proposing fixes."
                    ),
                )
            )

        # 5. Per-endpoint latency
        for ep in snapshot.endpoints:
            if (
                ep.request_count >= 5
                and ep.avg_latency_ms > self._latency_threshold_ms
            ):
                snapshot.anomalies.append(
                    Anomaly(
                        type=AnomalyType.HIGH_LATENCY,
                        severity="medium",
                        description=(
                            f"{ep.method} {ep.path} avg latency is "
                            f"{ep.avg_latency_ms:.0f}ms "
                            f"(threshold: {self._latency_threshold_ms:.0f}ms)."
                        ),
                        evidence={
                            "method": ep.method,
                            "path": ep.path,
                            "avg_latency_ms": ep.avg_latency_ms,
                        },
                        suggested_action=(
                            f"Profile {ep.path}. Consider adding DB indexes, "
                            "caching, or N+1 query fixes."
                        ),
                    )
                )

        # 6. Crashed containers
        for name, status in snapshot.container_states.items():
            if status in ("exited", "dead"):
                snapshot.anomalies.append(
                    Anomaly(
                        type=AnomalyType.SERVICE_UNREACHABLE,
                        severity="critical",
                        description=f"Container '{name}' is in '{status}' state.",
                        evidence={"container": name, "status": status},
                        suggested_action="Check container logs for startup errors.",
                    )
                )

        # 7. Schema drift (new tables appeared since last observation)
        if self._previous_schema and snapshot.schema:
            prev_tables = {t["name"] for t in self._previous_schema.tables}
            curr_tables = {t["name"] for t in snapshot.schema.tables}
            new_tables = curr_tables - prev_tables
            removed_tables = prev_tables - curr_tables
            if new_tables or removed_tables:
                snapshot.anomalies.append(
                    Anomaly(
                        type=AnomalyType.SCHEMA_DRIFT,
                        severity="low",
                        description="Database schema changed since last observation.",
                        evidence={
                            "added_tables": list(new_tables),
                            "removed_tables": list(removed_tables),
                        },
                        suggested_action="Verify the migration was intentional.",
                    )
                )

        if snapshot.schema:
            self._previous_schema = snapshot.schema
