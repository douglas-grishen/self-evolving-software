from __future__ import annotations

import json
from datetime import UTC, datetime

from engine.usage_tracker import UsageTracker


def test_sync_llm_config_signature_sets_initial_signature_without_reset(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.json")

    snapshot, reset_applied = tracker.sync_llm_config_signature("openai|gpt-5.4")

    assert reset_applied is False
    assert snapshot["llm_config_signature"] == "openai|gpt-5.4"
    assert snapshot["proactive_runs"] == 0
    assert snapshot["failed_evolutions"] == 0


def test_sync_llm_config_signature_resets_proactive_budget_on_change(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.json")
    today = datetime.now(UTC).date().isoformat()

    state = {
        "date": today,
        "updated_at": f"{today}T00:00:00+00:00",
        "llm_calls": 7,
        "input_tokens": 123,
        "output_tokens": 45,
        "proactive_runs": 10,
        "failed_evolutions": 10,
        "task_attempts": {"bootstrap": 3},
        "proactive_runs_by_task": {"bootstrap": 10},
        "providers": {
            "bedrock": {
                "llm_calls": 7,
                "input_tokens": 123,
                "output_tokens": 45,
                "models": {},
            }
        },
        "llm_config_signature": "bedrock|claude",
    }
    tracker.state_path.write_text(json.dumps(state), encoding="utf-8")

    snapshot, reset_applied = tracker.sync_llm_config_signature(
        "openai|gpt-5.4",
        reset_proactive_counters_on_change=True,
    )

    assert reset_applied is True
    assert snapshot["llm_config_signature"] == "openai|gpt-5.4"
    assert snapshot["proactive_runs"] == 0
    assert snapshot["failed_evolutions"] == 0
    assert snapshot["task_attempts"] == {}
    assert snapshot["proactive_runs_by_task"] == {}
    assert snapshot["llm_calls"] == 7
    assert snapshot["input_tokens"] == 123
    assert snapshot["output_tokens"] == 45
