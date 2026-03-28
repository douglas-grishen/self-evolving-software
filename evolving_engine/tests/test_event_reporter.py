"""Tests for lesson persistence helpers in EventReporter."""

from datetime import datetime, timezone

import httpx
import pytest

import engine.event_reporter as event_reporter_module
from engine.event_reporter import EventReporter
from engine.models.memory import EngineMemory


def _lesson(
    *,
    lesson_id: str = "lesson-1",
    category: str = "bug_fix",
    title: str = "Control-plane settings reads must tolerate schema drift",
    content: str = "Select only the columns you need.",
    severity: str = "warning",
    active: bool = True,
    times_reinforced: int = 2,
) -> EngineMemory:
    now = datetime.now(timezone.utc)
    return EngineMemory(
        id=lesson_id,
        category=category,
        title=title,
        content=content,
        source="auto",
        severity=severity,
        active=active,
        times_reinforced=times_reinforced,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_remember_lesson_reinforces_existing_match(monkeypatch):
    """Matching lessons should be reinforced instead of duplicated."""
    reporter = EventReporter("http://example.com")
    patch_calls: list[tuple[str, dict]] = []

    async def fake_fetch_lessons(active_only: bool = True):
        assert active_only is False
        return [_lesson()]

    async def fake_patch_lesson(lesson_id: str, payload: dict):
        patch_calls.append((lesson_id, payload))
        return True

    async def fake_post_lesson(**kwargs):
        raise AssertionError(f"Unexpected create: {kwargs}")

    monkeypatch.setattr(reporter, "fetch_lessons", fake_fetch_lessons)
    monkeypatch.setattr(reporter, "patch_lesson", fake_patch_lesson)
    monkeypatch.setattr(reporter, "post_lesson", fake_post_lesson)

    lesson_id = await reporter.remember_lesson(
        category="bug_fix",
        title="Control-plane settings reads must tolerate schema drift",
        content="Select only the columns you need.",
        severity="critical",
    )

    assert lesson_id == "lesson-1"
    assert patch_calls == [
        (
            "lesson-1",
            {"times_reinforced": 3, "severity": "critical"},
        )
    ]


@pytest.mark.asyncio
async def test_remember_lesson_creates_when_no_match(monkeypatch):
    """New lessons should still be created when no prior match exists."""
    reporter = EventReporter("http://example.com")

    async def fake_fetch_lessons(active_only: bool = True):
        assert active_only is False
        return []

    async def fake_post_lesson(**kwargs):
        return "lesson-2"

    monkeypatch.setattr(reporter, "fetch_lessons", fake_fetch_lessons)
    monkeypatch.setattr(reporter, "post_lesson", fake_post_lesson)

    lesson_id = await reporter.remember_lesson(
        category="best_practice",
        title="Instance incidents must harden the open-source framework",
        content="Always upstream live-instance fixes with regression coverage.",
        severity="warning",
    )

    assert lesson_id == "lesson-2"


@pytest.mark.asyncio
async def test_is_backend_available_uses_canonical_health_contract(monkeypatch):
    """Backend availability should require the canonical contract, not any non-500 response."""
    reporter = EventReporter("http://backend:8000")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/health":
            return httpx.Response(404, json={"detail": "Not Found"})
        if request.url.path == "/api/v1/system/info":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "status": "ok",
                    "timestamp": "2026-03-28T00:00:00Z",
                    "service": "backend",
                },
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(event_reporter_module.httpx, "AsyncClient", client_factory)

    assert await reporter.is_backend_available() is True
