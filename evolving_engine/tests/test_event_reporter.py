"""Tests for EventReporter payload normalization."""

from engine.event_reporter import EventReporter


def test_normalize_apps_payload_accepts_list_shape():
    payload = [{"id": "app-1", "name": "Delegate Setup"}]

    normalized = EventReporter._normalize_apps_payload(payload)

    assert normalized == payload


def test_normalize_apps_payload_accepts_legacy_wrapped_shape():
    payload = {"apps": [{"id": "app-1", "name": "Delegate Setup"}]}

    normalized = EventReporter._normalize_apps_payload(payload)

    assert normalized == payload["apps"]


def test_normalize_apps_payload_skips_invalid_items():
    payload = {"apps": ["broken-entry", {"id": "app-1", "name": "Delegate Setup"}]}

    normalized = EventReporter._normalize_apps_payload(payload)

    assert normalized == [{"id": "app-1", "name": "Delegate Setup"}]
