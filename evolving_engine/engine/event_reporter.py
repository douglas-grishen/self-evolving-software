"""EventReporter — fire-and-forget communication with the backend API.

The engine uses this client to:
  - POST evolution lifecycle events to the backend (for the UI)
  - Poll for pending Inceptions submitted via the UI
  - Report Inception processing results back to the backend
  - Store Purpose versions in the backend DB

All operations are fire-and-forget: if the backend is temporarily unavailable,
the engine continues operating. Events are always logged locally via structlog.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from engine.context import EvolutionContext
from engine.models.inception import InceptionRequest, InceptionResult, InceptionSource
from engine.models.purpose import Purpose

logger = structlog.get_logger()

# Timeout for API calls — short to avoid blocking the engine
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class EventReporter:
    """Lightweight HTTP client for engine → backend communication."""

    def __init__(self, backend_url: str) -> None:
        self.base_url = backend_url.rstrip("/")
        self._evolution_url = f"{self.base_url}/api/v1/evolution"

    # ------------------------------------------------------------------
    # Evolution Events
    # ------------------------------------------------------------------

    async def post_event(self, ctx: EvolutionContext) -> None:
        """Report an evolution lifecycle event to the backend."""
        payload: dict[str, Any] = {
            "request_id": ctx.request_id,
            "status": ctx.status.value,
            "source": ctx.request.source.value,
            "user_request": ctx.request.user_request[:2000],
        }

        if ctx.plan:
            payload["plan_summary"] = ctx.plan.summary
            payload["risk_level"] = ctx.plan.risk_level

        if ctx.validation_result:
            payload["validation_passed"] = ctx.validation_result.passed

        if ctx.deployment_result:
            payload["deployment_success"] = ctx.deployment_result.success
            payload["commit_sha"] = ctx.deployment_result.commit_sha or None
            payload["branch"] = ctx.deployment_result.branch or None

        if ctx.error:
            payload["error"] = ctx.error

        if ctx.status.value in ("completed", "failed"):
            payload["completed_at"] = datetime.now(timezone.utc).isoformat()

        if ctx.history:
            payload["events_json"] = [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "agent": e.agent,
                    "action": e.action,
                    "status": e.status,
                    "details": e.details,
                }
                for e in ctx.history
            ]

        await self._post(f"{self._evolution_url}/events", payload)

    # ------------------------------------------------------------------
    # Inceptions
    # ------------------------------------------------------------------

    async def poll_inceptions(self) -> list[InceptionRequest]:
        """Poll the backend for pending inception requests."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{self._evolution_url}/inceptions",
                    params={"status": "pending"},
                )
                resp.raise_for_status()
                data = resp.json()

            inceptions = []
            for item in data:
                inceptions.append(
                    InceptionRequest(
                        id=item["id"],
                        source=InceptionSource(item["source"]),
                        directive=item["directive"],
                        rationale=item.get("rationale", ""),
                        submitted_at=item["submitted_at"],
                        status=item["status"],
                    )
                )

            if inceptions:
                logger.info("event_reporter.inceptions_found", count=len(inceptions))

            return inceptions

        except Exception as exc:
            logger.debug("event_reporter.poll_inceptions_error", error=str(exc))
            return []

    async def report_inception_result(
        self, inception_id: str, result: InceptionResult, accepted: bool
    ) -> None:
        """Report the outcome of processing an inception."""
        payload = {
            "status": "applied" if accepted else "rejected",
            "processed_at": result.applied_at.isoformat(),
            "previous_purpose_version": result.previous_purpose_version,
            "new_purpose_version": result.new_purpose_version,
            "changes_summary": result.changes_summary,
        }
        await self._put(f"{self._evolution_url}/inceptions/{inception_id}", payload)

    # ------------------------------------------------------------------
    # Purpose
    # ------------------------------------------------------------------

    async def post_purpose(self, purpose: Purpose, inception_id: str | None = None) -> None:
        """Store a purpose version in the backend DB."""
        payload = {
            "version": purpose.version,
            "content_yaml": purpose.to_yaml_string(),
            "inception_id": inception_id,
        }
        await self._post(f"{self._evolution_url}/purpose", payload)

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    async def _post(self, url: str, payload: dict) -> None:
        """Fire-and-forget POST."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            logger.debug("event_reporter.post_ok", url=url)
        except Exception as exc:
            logger.debug("event_reporter.post_error", url=url, error=str(exc))

    async def _put(self, url: str, payload: dict) -> None:
        """Fire-and-forget PUT."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.put(url, json=payload)
                resp.raise_for_status()
            logger.debug("event_reporter.put_ok", url=url)
        except Exception as exc:
            logger.debug("event_reporter.put_error", url=url, error=str(exc))
