"""Tests for runtime contract detection in the observer."""

from datetime import datetime
from types import SimpleNamespace
import sys

import httpx
import pytest

from engine.monitor.models import ContractProbeFailure, HealthCheck
from engine.monitor.observer import RuntimeObserver


@pytest.mark.asyncio
async def test_observer_promotes_runtime_contract_failures_to_anomalies(tmp_path, monkeypatch):
    """Mounted app contract failures should become first-class anomalies."""
    app_dir = tmp_path / "frontend" / "src" / "apps" / "example-app"
    app_dir.mkdir(parents=True)
    (app_dir / "index.tsx").write_text("export default function App() { return null; }")

    observer = RuntimeObserver(
        base_url="http://backend:8000",
        managed_app_path=tmp_path,
    )

    async def fake_health(_client):
        return HealthCheck(
            status="ok",
            checks={"app": "ok", "database": "ok"},
            db_latency_ms=3.0,
            app_version="1.0.0",
            environment="test",
            timestamp=datetime.utcnow(),
        )

    async def fake_metrics(_client):
        return {
            "total_requests": 2,
            "total_errors": 0,
            "global_error_rate": 0.0,
            "uptime_seconds": 30.0,
            "endpoints": [],
        }

    async def fake_errors(_client):
        return []

    async def fake_schema(_client):
        return Exception("schema probe intentionally skipped")

    async def fake_contracts(_client):
        return [
            ContractProbeFailure(
                app_key="example-app",
                method="POST",
                path="/api/v1/example-app/items/search",
                description="Example App item search endpoint",
                expected_statuses=[200],
                status_code=405,
                detail='{"detail":"Method Not Allowed"}',
            )
        ]

    async def fake_docker_states():
        return {}

    monkeypatch.setattr(observer, "_probe_health", fake_health)
    monkeypatch.setattr(observer, "_probe_metrics", fake_metrics)
    monkeypatch.setattr(observer, "_probe_errors", fake_errors)
    monkeypatch.setattr(observer, "_probe_schema", fake_schema)
    monkeypatch.setattr(observer, "_probe_runtime_contracts", fake_contracts)
    monkeypatch.setattr(observer, "_probe_docker_states", fake_docker_states)

    snapshot = await observer.observe()

    assert len(snapshot.contract_failures) == 1
    assert snapshot.contract_failures[0].status_code == 405
    assert any(anomaly.type.value == "missing_endpoint" for anomaly in snapshot.anomalies)
    assert any("/api/v1/example-app/items/search" in anomaly.description for anomaly in snapshot.anomalies)


@pytest.mark.asyncio
async def test_observer_probes_core_framework_contracts_without_app_metadata():
    """Framework routes should be probed even when no mounted app contracts exist."""
    observer = RuntimeObserver(base_url="http://backend:8000")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-ses-probe") == "runtime-contract"
        if request.method == "GET" and request.url.path == "/api/v1/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET" and request.url.path == "/api/v1/system/info":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "status": "ok",
                    "timestamp": "2026-03-28T00:00:00Z",
                    "service": "backend",
                },
            )
        if request.method == "GET" and request.url.path == "/api/v1/evolution/status":
            return httpx.Response(
                200,
                json={
                    "total_evolutions": 0,
                    "active_evolutions": 0,
                    "completed_evolutions": 0,
                    "failed_evolutions": 0,
                    "current_purpose_version": 4,
                    "pending_inceptions": 0,
                    "last_evolution": None,
                },
            )
        if request.method == "GET" and request.url.path == "/api/v1/apps":
            return httpx.Response(200, json=[])
        if request.method == "POST" and request.url.path == "/api/v1/apps":
            return httpx.Response(422, json={"detail": "missing name"})
        if request.method == "POST" and request.url.path == "/api/v1/apps/capabilities":
            return httpx.Response(422, json={"detail": "missing name"})
        if request.method == "POST" and request.url.path == "/api/v1/chat":
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="http://backend:8000",
        transport=transport,
    ) as client:
        failures = await observer._probe_runtime_contracts(client)

    assert isinstance(failures, list)
    assert any(failure.path == "/api/v1/chat" for failure in failures)
    assert any(failure.app_key == "framework" for failure in failures)


@pytest.mark.asyncio
async def test_observer_reads_operational_plane_container_states_from_legacy_and_new_labels(
    monkeypatch,
):
    """The observer should recognize both new and legacy subsystem labels during rollout."""
    observer = RuntimeObserver(base_url="http://backend:8000")

    containers = [
        SimpleNamespace(
            name="backend",
            status="running",
            labels={"com.ses.subsystem": "operational-plane"},
        ),
        SimpleNamespace(
            name="frontend",
            status="running",
            labels={"com.ses.subsystem": "managed-system"},
        ),
        SimpleNamespace(
            name="engine",
            status="running",
            labels={"com.ses.subsystem": "evolution-plane"},
        ),
    ]
    fake_client = SimpleNamespace(
        containers=SimpleNamespace(list=lambda all=True: containers)
    )
    monkeypatch.setitem(
        sys.modules,
        "docker",
        SimpleNamespace(from_env=lambda: fake_client),
    )

    states = await observer._probe_docker_states()

    assert states == {"backend": "running", "frontend": "running"}
