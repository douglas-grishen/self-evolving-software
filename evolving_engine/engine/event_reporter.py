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

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from engine.context import EvolutionContext
from engine.models.backlog import BacklogItem, BacklogPlanItem
from engine.models.inception import InceptionRequest, InceptionResult, InceptionSource
from engine.models.memory import EngineMemory
from engine.models.purpose import Purpose
from engine.models.skills import AvailableSkill
from engine.runtime_contracts import (
    get_core_availability_probes,
    validate_runtime_contract_response,
)

logger = structlog.get_logger()

# Timeout for API calls — short to avoid blocking the engine
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_RETRY_BASE_DELAY_SECONDS = 1.0
_RETRY_MAX_DELAY_SECONDS = 8.0


def _normalize_lesson_key(value: str) -> str:
    """Normalize lesson identifiers so near-identical titles dedupe cleanly."""
    return re.sub(r"\s+", " ", value.strip().lower())


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

        # Structured events_json: plan file changes + audit history
        events_data: dict[str, Any] = {}
        if ctx.plan:
            events_data["plan_changes"] = [
                {
                    "file_path": c.file_path,
                    "action": c.action,
                    "description": c.description,
                    "layer": c.layer,
                }
                for c in ctx.plan.changes
            ]
        if ctx.history:
            events_data["history"] = [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "agent": e.agent,
                    "action": e.action,
                    "status": e.status,
                    "details": e.details,
                }
                for e in ctx.history
            ]
        if events_data:
            payload["events_json"] = events_data

        # The final event is posted immediately after deploy. At that point the
        # backend may still be restarting, so give terminal states extra retries
        # to avoid leaving dashboard rows stuck in "received".
        retries = 8 if ctx.status.value in ("completed", "failed") else 1
        await self._post(f"{self._evolution_url}/events", payload, retries=retries)

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
        await self._put(f"{self._evolution_url}/inceptions/{inception_id}", payload, retries=3)

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

    async def post_purpose(self, purpose: Purpose, inception_id: str | None = None) -> bool:
        """Store a purpose version in the backend DB."""
        payload = {
            "version": purpose.version,
            "content_yaml": purpose.to_yaml_string(),
            "inception_id": inception_id,
        }
        return await self._post(f"{self._evolution_url}/purpose", payload, retries=8)

    # ------------------------------------------------------------------
    # Proactive Backlog
    # ------------------------------------------------------------------

    async def fetch_backlog(
        self,
        purpose_version: int | None = None,
        include_completed: bool = True,
    ) -> list[BacklogItem] | None:
        """Fetch persisted proactive backlog items from the backend."""
        params: dict[str, Any] = {"include_completed": str(include_completed).lower()}
        if purpose_version is not None:
            params["purpose_version"] = purpose_version

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(f"{self._evolution_url}/backlog", params=params)
                resp.raise_for_status()
                data = resp.json()
            return [BacklogItem.model_validate(item) for item in data]
        except Exception as exc:
            logger.debug("event_reporter.fetch_backlog_error", error=str(exc))
            return None

    async def fetch_skills(self) -> list[AvailableSkill]:
        """Fetch the runtime skill inventory from the backend."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(f"{self.base_url}/api/v1/skills")
                resp.raise_for_status()
                data = resp.json()
            return [AvailableSkill.model_validate(item) for item in data]
        except Exception as exc:
            logger.debug("event_reporter.fetch_skills_error", error=str(exc))
            return []

    async def sync_backlog(
        self,
        purpose_version: int,
        items: list[BacklogPlanItem],
    ) -> list[BacklogItem] | None:
        """Persist the current proactive roadmap for the active Purpose version."""
        payload = {
            "purpose_version": purpose_version,
            "items": [item.model_dump(mode="json") for item in items],
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(f"{self._evolution_url}/backlog/sync", json=payload)
                resp.raise_for_status()
                data = resp.json()
            return [BacklogItem.model_validate(item) for item in data]
        except Exception as exc:
            logger.debug("event_reporter.sync_backlog_error", error=str(exc))
            return None

    async def update_backlog_item(self, item_id: str, payload: dict[str, Any]) -> BacklogItem | None:
        """Update runtime execution state for a proactive backlog item."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.put(f"{self._evolution_url}/backlog/{item_id}", json=payload)
                resp.raise_for_status()
                data = resp.json()
            return BacklogItem.model_validate(data)
        except Exception as exc:
            logger.debug(
                "event_reporter.update_backlog_item_error",
                item_id=item_id,
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------
    # Apps, Features & Capabilities
    # ------------------------------------------------------------------

    async def fetch_apps(self) -> list[dict] | None:
        """Fetch the list of all apps from the backend."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(f"{self.base_url}/api/v1/apps")
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.debug("event_reporter.fetch_apps_error", error=str(exc))
            return None

    async def is_backend_available(self) -> bool:
        """Check whether the backend control-plane is currently reachable."""
        for probe in get_core_availability_probes():
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
                    resp = await client.request(
                        probe.method,
                        f"{self.base_url}{probe.path}",
                        json=probe.json_body,
                    )
                    if validate_runtime_contract_response(probe, resp) is None:
                        return True
            except Exception:
                continue
        return False

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

    async def update_app(self, app_id: str, payload: dict) -> bool:
        """Update an app's status or metadata via the backend API."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.put(f"{self.base_url}/api/v1/apps/{app_id}", json=payload)
                resp.raise_for_status()
            logger.info("event_reporter.app_updated", app_id=app_id, **payload)
            return True
        except Exception as exc:
            logger.debug("event_reporter.update_app_error", error=str(exc))
            return False

    async def get_setting(self, key: str) -> str | None:
        """Fetch a single system setting value from the backend API."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(f"{self.base_url}/api/v1/settings/{key}")
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json().get("value")
        except Exception as exc:
            logger.debug("event_reporter.get_setting_error", key=key, error=str(exc))
            return None

    async def set_setting(self, key: str, value: str) -> bool:
        """Persist a single system setting value through the backend API."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.put(
                    f"{self.base_url}/api/v1/settings/{key}",
                    json={"value": value},
                )
                resp.raise_for_status()
            return True
        except Exception as exc:
            logger.debug("event_reporter.set_setting_error", key=key, error=str(exc))
            return False

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
    # Engine Memory (Lessons Learned)
    # ------------------------------------------------------------------

    async def fetch_lessons(self, active_only: bool = True) -> list[EngineMemory]:
        """Fetch all active lessons from the backend memory store.

        Called by DataManagerAgent at the start of every evolution cycle.
        Returns an empty list on any error — the engine continues without lessons
        rather than blocking on a backend unavailability.
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/memory",
                    params={"active_only": str(active_only).lower()},
                )
                resp.raise_for_status()
                data = resp.json()
            lessons = [EngineMemory.from_api_dict(item) for item in data]
            if lessons:
                logger.info(
                    "event_reporter.lessons_fetched",
                    count=len(lessons),
                    critical=sum(1 for l in lessons if l.severity == "critical"),
                    warning=sum(1 for l in lessons if l.severity == "warning"),
                )
            return lessons
        except Exception as exc:
            logger.debug("event_reporter.fetch_lessons_error", error=str(exc))
            return []

    async def patch_lesson(self, lesson_id: str, payload: dict[str, Any]) -> bool:
        """Update an existing lesson in the backend memory store."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.patch(
                    f"{self.base_url}/api/v1/memory/{lesson_id}",
                    json=payload,
                )
                resp.raise_for_status()
            logger.info(
                "event_reporter.lesson_patched",
                lesson_id=lesson_id,
                fields=sorted(payload.keys()),
            )
            return True
        except Exception as exc:
            logger.debug(
                "event_reporter.patch_lesson_error",
                lesson_id=lesson_id,
                error=str(exc),
            )
            return False

    async def post_lesson(
        self,
        category: str,
        title: str,
        content: str,
        severity: str = "warning",
        source: str = "auto",
    ) -> str | None:
        """Post a new lesson to the backend memory store.

        Called by the orchestrator after analyzing a failed or retried evolution.
        Returns the new lesson ID, or None on error.
        """
        payload = {
            "category": category,
            "title": title,
            "content": content,
            "severity": severity,
            "source": source,
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v1/memory",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
            lesson_id = data.get("id")
            logger.info(
                "event_reporter.lesson_posted",
                lesson_id=lesson_id,
                severity=severity,
                title=title[:80],
            )
            return lesson_id
        except Exception as exc:
            logger.debug("event_reporter.post_lesson_error", error=str(exc))
            return None

    async def remember_lesson(
        self,
        category: str,
        title: str,
        content: str,
        severity: str = "warning",
        source: str = "auto",
    ) -> str | None:
        """Create a lesson or reinforce an existing matching one.

        Matching is intentionally conservative: category + normalized title.
        This keeps repeated incident learnings from flooding the memory store.
        """
        lessons = await self.fetch_lessons(active_only=False)
        existing = self._find_matching_lesson(lessons, category=category, title=title)

        if existing is None:
            return await self.post_lesson(
                category=category,
                title=title,
                content=content,
                severity=severity,
                source=source,
            )

        if not existing.active:
            logger.info(
                "event_reporter.lesson_match_inactive",
                lesson_id=existing.id,
                title=title[:80],
            )
            return existing.id

        patch_payload: dict[str, Any] = {
            "times_reinforced": max(existing.times_reinforced, 0) + 1,
        }
        if severity == "critical" and existing.severity != "critical":
            patch_payload["severity"] = severity

        updated = await self.patch_lesson(existing.id, patch_payload)
        if updated:
            logger.info(
                "event_reporter.lesson_reinforced",
                lesson_id=existing.id,
                title=title[:80],
                times_reinforced=patch_payload["times_reinforced"],
            )
            return existing.id
        return None

    @staticmethod
    def _find_matching_lesson(
        lessons: list[EngineMemory],
        *,
        category: str,
        title: str,
    ) -> EngineMemory | None:
        """Return the first lesson whose category/title already match this one."""
        normalized_category = _normalize_lesson_key(category)
        normalized_title = _normalize_lesson_key(title)
        for lesson in lessons:
            if (
                _normalize_lesson_key(lesson.category) == normalized_category
                and _normalize_lesson_key(lesson.title) == normalized_title
            ):
                return lesson
        return None

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    async def _post(self, url: str, payload: dict, retries: int = 1) -> bool:
        """Fire-and-forget POST."""
        for attempt in range(1, retries + 1):
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                logger.debug("event_reporter.post_ok", url=url, attempt=attempt)
                return True
            except Exception as exc:
                if attempt >= retries:
                    logger.debug("event_reporter.post_error", url=url, error=str(exc), attempt=attempt)
                    return False

                delay = min(
                    _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
                    _RETRY_MAX_DELAY_SECONDS,
                )
                logger.debug(
                    "event_reporter.post_retry",
                    url=url,
                    error=str(exc),
                    attempt=attempt,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)

        return False

    async def _put(self, url: str, payload: dict, retries: int = 1) -> bool:
        """Fire-and-forget PUT."""
        for attempt in range(1, retries + 1):
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.put(url, json=payload)
                    resp.raise_for_status()
                logger.debug("event_reporter.put_ok", url=url, attempt=attempt)
                return True
            except Exception as exc:
                if attempt >= retries:
                    logger.debug("event_reporter.put_error", url=url, error=str(exc), attempt=attempt)
                    return False

                delay = min(
                    _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
                    _RETRY_MAX_DELAY_SECONDS,
                )
                logger.debug(
                    "event_reporter.put_retry",
                    url=url,
                    error=str(exc),
                    attempt=attempt,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)

        return False
