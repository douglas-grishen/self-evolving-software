"""Tests for runtime metrics error classification."""

import pytest
from starlette.requests import Request
from starlette.responses import Response

import app.middleware.metrics as metrics_module
from app.middleware.metrics import MetricsMiddleware


def _make_request(path: str = "/api/v1/apps") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def _reset_metrics_state() -> None:
    metrics_module._request_count = 0
    metrics_module._error_count = 0
    metrics_module._client_error_count = 0
    metrics_module._endpoint_stats.clear()
    metrics_module._recent_errors.clear()
    metrics_module._recent_slow.clear()


@pytest.mark.asyncio
async def test_client_409_does_not_increment_runtime_error_rate():
    _reset_metrics_state()
    middleware = MetricsMiddleware(app=lambda scope, receive, send: None)

    async def call_next(_request: Request) -> Response:
        return Response(status_code=409)

    await middleware.dispatch(_make_request(), call_next)

    snapshot = metrics_module.get_metrics_snapshot()
    assert snapshot["total_requests"] == 1
    assert snapshot["total_errors"] == 0
    assert snapshot["total_client_errors"] == 1
    assert metrics_module.get_recent_errors() == []


@pytest.mark.asyncio
async def test_server_503_increments_runtime_error_rate():
    _reset_metrics_state()
    middleware = MetricsMiddleware(app=lambda scope, receive, send: None)

    async def call_next(_request: Request) -> Response:
        return Response(status_code=503)

    await middleware.dispatch(_make_request("/api/v1/search"), call_next)

    snapshot = metrics_module.get_metrics_snapshot()
    assert snapshot["total_requests"] == 1
    assert snapshot["total_errors"] == 1
    assert snapshot["global_error_rate"] == 1.0
    assert metrics_module.get_recent_errors()[0]["status_code"] == 503
