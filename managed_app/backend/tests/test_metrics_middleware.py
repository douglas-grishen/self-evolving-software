"""Tests for excluding internal runtime probes from operational error metrics."""

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.middleware import metrics as metrics_module
from app.middleware.metrics import MetricsMiddleware


def _reset_metrics_state() -> None:
    metrics_module._request_count = 0
    metrics_module._error_count = 0
    metrics_module._endpoint_stats.clear()
    metrics_module._recent_errors.clear()
    metrics_module._recent_slow.clear()


def _build_request(path: str, headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (key.lower().encode("utf-8"), value.encode("utf-8"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "scheme": "http",
            "query_string": b"",
            "headers": raw_headers,
            "client": ("testclient", 1234),
            "server": ("testserver", 80),
        }
    )


@pytest.mark.asyncio
async def test_runtime_contract_probe_422_is_excluded_from_error_metrics():
    """Expected validation failures from internal contract probes should not page the engine."""
    _reset_metrics_state()
    middleware = MetricsMiddleware(app=lambda scope, receive, send: None)
    
    async def _call_next(_request: Request) -> JSONResponse:
        return JSONResponse({"detail": "missing name"}, status_code=422)

    response = await middleware.dispatch(
        _build_request("/api/v1/apps", headers={"X-SES-Probe": "runtime-contract"}),
        _call_next,
    )

    assert response.status_code == 422
    assert metrics_module.get_metrics_snapshot()["total_requests"] == 0
    assert metrics_module.get_metrics_snapshot()["total_errors"] == 0
    assert metrics_module.get_recent_errors() == []


@pytest.mark.asyncio
async def test_normal_422_still_counts_as_runtime_error():
    """Regular client validation failures should still appear in runtime metrics."""
    _reset_metrics_state()
    middleware = MetricsMiddleware(app=lambda scope, receive, send: None)
    
    async def _call_next(_request: Request) -> JSONResponse:
        return JSONResponse({"detail": "missing name"}, status_code=422)

    response = await middleware.dispatch(
        _build_request("/api/v1/apps"),
        _call_next,
    )

    assert response.status_code == 422
    snapshot = metrics_module.get_metrics_snapshot()
    assert snapshot["total_requests"] == 1
    assert snapshot["total_errors"] == 1
    assert snapshot["global_error_rate"] == 1.0
    assert metrics_module.get_recent_errors()[0]["status_code"] == 422
