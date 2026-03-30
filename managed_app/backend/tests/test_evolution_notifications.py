"""Tests for persistent evolution notifications."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.api.v1.evolution as evolution_api
from app.api.v1.evolution import (
    _create_or_update_notification,
    _notification_webhook_payload,
    _schedule_external_notification,
    _should_forward_notification,
    acknowledge_notification,
)
from app.models.evolution import SystemNotificationRecord
from app.schemas.evolution import SystemNotificationCreate


class _ExecuteResult:
    def __init__(self, record=None):
        self._record = record

    def scalar_one_or_none(self):
        return self._record


class _FakeNotificationSession:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []

    async def execute(self, stmt):
        return _ExecuteResult(self.existing)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        target = self.added[-1] if self.added else self.existing
        if target is None:
            return
        if getattr(target, "id", None) is None:
            target.id = "notif-1"
        target.created_at = target.created_at or datetime.now(UTC)
        target.updated_at = target.updated_at or datetime.now(UTC)


@pytest.mark.asyncio
async def test_create_notification_starts_unacknowledged_with_zero_updates():
    db = _FakeNotificationSession()

    record = await _create_or_update_notification(
        SystemNotificationCreate(
            source="engine",
            kind="evolution_blocker",
            severity="critical",
            message="Evolution is blocked because the backend is unavailable.",
        ),
        db,
    )

    assert record.id == "notif-1"
    assert record.acknowledged is False
    assert record.update_count == 0
    assert record.message_hash


@pytest.mark.asyncio
async def test_refreshing_same_notification_resets_acknowledgement_and_increments_updates():
    existing = SystemNotificationRecord(
        source="engine",
        kind="evolution_blocker",
        severity="high",
        message="Evolution is blocked because no Purpose is defined.",
        message_hash="same-hash",
        acknowledged=True,
        acknowledged_at=datetime.now(UTC),
        update_count=2,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    existing.id = "notif-7"
    db = _FakeNotificationSession(existing=existing)

    record = await _create_or_update_notification(
        SystemNotificationCreate(
            source="engine",
            kind="evolution_blocker",
            severity="critical",
            message="Evolution is blocked because no Purpose is defined.",
        ),
        db,
    )

    assert record is existing
    assert record.severity == "critical"
    assert record.acknowledged is False
    assert record.acknowledged_at is None
    assert record.update_count == 3


@pytest.mark.asyncio
async def test_acknowledge_notification_marks_row_as_acknowledged():
    record = SystemNotificationRecord(
        source="engine",
        kind="evolution_blocker",
        severity="critical",
        message="Evolution is blocked because proactive planning failed.",
        message_hash="hash",
        acknowledged=False,
        update_count=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    record.id = "notif-9"
    db = _FakeNotificationSession(existing=record)

    updated = await acknowledge_notification(
        "notif-9",
        db=db,
        _admin=SimpleNamespace(username="admin"),
    )

    assert updated.acknowledged is True
    assert updated.acknowledged_at is not None


def test_should_forward_notification_respects_threshold(monkeypatch):
    monkeypatch.setattr(evolution_api.settings, "notification_webhook_url", "https://alerts.example.test")
    monkeypatch.setattr(evolution_api.settings, "notification_webhook_min_severity", "high")

    assert _should_forward_notification("critical") is True
    assert _should_forward_notification("high") is True
    assert _should_forward_notification("medium") is False


def test_schedule_external_notification_enqueues_background_task(monkeypatch):
    monkeypatch.setattr(evolution_api.settings, "notification_webhook_url", "https://alerts.example.test")
    monkeypatch.setattr(evolution_api.settings, "notification_webhook_min_severity", "critical")

    record = SystemNotificationRecord(
        source="engine",
        kind="evolution_blocker",
        severity="critical",
        message="Backend unavailable",
        message_hash="hash",
        acknowledged=False,
        update_count=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    record.id = "notif-42"

    captured = []

    class _BackgroundTasks:
        def add_task(self, func, payload):
            captured.append((func, payload))

    _schedule_external_notification(_BackgroundTasks(), record)

    assert len(captured) == 1
    func, payload = captured[0]
    assert func is evolution_api._deliver_external_notification
    assert payload["id"] == "notif-42"
    assert payload["severity"] == "critical"


def test_notification_webhook_payload_serializes_timestamps():
    now = datetime.now(UTC)
    record = SystemNotificationRecord(
        source="engine",
        kind="evolution_blocker",
        severity="critical",
        message="Runtime contracts failed",
        message_hash="hash",
        acknowledged=False,
        update_count=2,
        created_at=now,
        updated_at=now,
    )
    record.id = "notif-55"

    payload = _notification_webhook_payload(record)

    assert payload["created_at"] == now.isoformat()
    assert payload["updated_at"] == now.isoformat()
    assert payload["update_count"] == 2
