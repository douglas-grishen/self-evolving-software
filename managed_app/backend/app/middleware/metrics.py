"""Request metrics middleware.

Tracks every HTTP request passing through the backend so the Autonomic Manager
can observe runtime behavior: latency, error rates, endpoint usage patterns.

Data is stored in-memory (ring buffers) and exposed via /api/v1/monitor/metrics
and /api/v1/monitor/errors on the control-plane network.
"""

import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ---------------------------------------------------------------------------
# In-memory storage (ring buffers — survives until process restart)
# ---------------------------------------------------------------------------

_MAX_ERRORS = 200       # last N errors kept in memory
_MAX_SLOW = 100         # last N slow requests kept in memory
_SLOW_THRESHOLD_MS = 500
_INTERNAL_PROBE_HEADER = "x-ses-probe"
_INTERNAL_PROBE_VALUES = {"runtime-contract"}

# Counters
_request_count: int = 0
_error_count: int = 0
_startup_time: float = time.time()

# Per-endpoint stats: {method:path -> {"count", "total_ms", "errors"}}
_endpoint_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "total_ms": 0.0, "errors": 0})

# Recent errors ring buffer
_recent_errors: deque = deque(maxlen=_MAX_ERRORS)

# Recent slow requests ring buffer
_recent_slow: deque = deque(maxlen=_MAX_SLOW)


# ---------------------------------------------------------------------------
# Middleware class
# ---------------------------------------------------------------------------

class MetricsMiddleware(BaseHTTPMiddleware):
    """Intercepts every request to record latency and error information."""

    async def dispatch(self, request: Request, call_next) -> Response:
        global _request_count, _error_count

        start = time.monotonic()
        key = f"{request.method}:{request.url.path}"
        status_code = 500
        is_internal_probe = (
            request.headers.get(_INTERNAL_PROBE_HEADER, "").strip().lower()
            in _INTERNAL_PROBE_VALUES
        )

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            if not is_internal_probe:
                _recent_errors.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "method": request.method,
                    "path": request.url.path,
                    "error_type": type(exc).__name__,
                    "detail": str(exc)[:300],
                    "status_code": 500,
                })
                _error_count += 1
            raise
        finally:
            if not is_internal_probe:
                elapsed_ms = (time.monotonic() - start) * 1000
                _request_count += 1
                stats = _endpoint_stats[key]
                stats["count"] += 1
                stats["total_ms"] += elapsed_ms

                if status_code >= 400:
                    stats["errors"] += 1
                    _error_count += 1
                    _recent_errors.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": status_code,
                        "latency_ms": round(elapsed_ms, 2),
                    })

                if elapsed_ms > _SLOW_THRESHOLD_MS:
                    _recent_slow.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "method": request.method,
                        "path": request.url.path,
                        "latency_ms": round(elapsed_ms, 2),
                        "status_code": status_code,
                    })


# ---------------------------------------------------------------------------
# Public API — called by /api/v1/monitor/metrics and /api/v1/monitor/errors
# ---------------------------------------------------------------------------

def get_metrics_snapshot() -> dict:
    """Return an aggregated snapshot of all runtime metrics."""
    uptime_seconds = round(time.time() - _startup_time, 1)
    error_rate = round(_error_count / _request_count, 4) if _request_count else 0.0

    endpoints = []
    for key, stats in _endpoint_stats.items():
        method, _, path = key.partition(":")
        avg_ms = stats["total_ms"] / stats["count"] if stats["count"] else 0.0
        ep_error_rate = stats["errors"] / stats["count"] if stats["count"] else 0.0
        endpoints.append({
            "method": method,
            "path": path,
            "request_count": stats["count"],
            "avg_latency_ms": round(avg_ms, 2),
            "error_rate": round(ep_error_rate, 4),
            "error_count": stats["errors"],
        })

    # Sort by request count descending
    endpoints.sort(key=lambda e: e["request_count"], reverse=True)

    return {
        "uptime_seconds": uptime_seconds,
        "total_requests": _request_count,
        "total_errors": _error_count,
        "global_error_rate": error_rate,
        "slow_request_threshold_ms": _SLOW_THRESHOLD_MS,
        "recent_slow_requests": list(_recent_slow)[-10:],
        "endpoints": endpoints,
    }


def get_recent_errors() -> list[dict]:
    """Return the most recent errors (newest first)."""
    return list(reversed(list(_recent_errors)))
