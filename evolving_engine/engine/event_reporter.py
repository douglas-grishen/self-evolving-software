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

    async def fetch_purpose(self) -> Purpose | None:
        """Fetch the current Purpose from the backend DB.

        This is used at engine startup so the engine works with
        the Purpose defined by the admin via the UI — not a local file.
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(f"{self._evolution_url}/purpose")
                resp.raise_for_status()
                data = resp.json()

            if data is None or data.get("content_yaml") is None:
                logger.info("event_reporter.no_purpose_in_db")
                return None

            purpose = Purpose.from_yaml_string(data["content_yaml"])
            logger.info(
                "event_reporter.purpose_fetched",
                version=purpose.version,
                identity=purpose.identity.name,
            )
            return purpose

        except Exception as exc:
            logger.debug("event_reporter.fetch_purpose_error", error=str(exc))
            return None

    async def check_analysis_trigger(self) -> bool:
        """Check if an on-demand proactive analysis was triggered via the UI.

        Returns True if triggered (consumes the flag on the backend side).
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(f"{self._evolution_url}/trigger-analysis")
                resp.raise_for_status()
                data = resp.json()
            return data.get("triggered", False)
        except Exception as exc:
            logger.debug("event_reporter.check_trigger_error", error=str(exc))
            return False

    async def post_purpose(self, purpose: Purpose, inception_id: str | None = None) -> None:
        """Store a purpose version in the backend DB."""
        payload = {
            "version": purpose.version,
            "content_yaml": purpose.to_yaml_string(),
            "inception_id": inception_id,
        }
        await self._post(f"{self._evolution_url}/purpose", payload)

    # ------------------------------------------------------------------
    # Apps, Features & Capabilities
    # ------------------------------------------------------------------

    async def fetch_apps(self) -> list[dict]:
        """Fetch the list of all apps from the backend."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(f"{self.base_url}/api/v1/apps")
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.debug("event_reporter.fetch_apps_error", error=str(exc))
            return []

    async def create_app(self, payload: dict) -> str | None:
        """Create a new app via the backend API. Returns the app ID."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(f"{self.base_url}/api/v1/apps", json=payload)
                resp.raise_for_status()
                data = resp.json()
            app_id = data.get("id")
            logger.info("event_reporter.app_created", app_id=app_id, name=payload.get("name"))
            return app_id
        except Exception as exc:
            logger.debug("event_reporter.create_app_error", error=str(exc))
            return None

    async def create_capability(self, payload: dict) -> str | None:
        """Create a capability via the backend API. Returns the capability ID."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(f"{self.base_url}/api/v1/apps/capabilities", json=payload)
                resp.raise_for_status()
                data = resp.json()
            cap_id = data.get("id")
            logger.info("event_reporter.capability_created", cap_id=cap_id, name=payload.get("name"))
            return cap_id
        except Exception as exc:
            logger.debug("event_reporter.create_capability_error", error=str(exc))
            return None

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
