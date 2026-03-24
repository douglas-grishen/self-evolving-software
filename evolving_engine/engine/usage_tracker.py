"""Persist a tiny UTC daily usage ledger for engine cost/risk guardrails."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_STATE_LOCK = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today_key() -> str:
    return _utcnow().date().isoformat()


class UsageTracker:
    """Small file-backed counter store shared by providers and orchestrator."""

    def __init__(self, state_path: Path | str) -> None:
        self.state_path = Path(state_path)

    def snapshot(self) -> dict[str, Any]:
        """Return today's usage state, resetting automatically at UTC day boundaries."""
        with _STATE_LOCK:
            state = self._load_unlocked()
            self._write_unlocked(state)
            return json.loads(json.dumps(state))

    def task_attempts_today(self, task_key: str) -> int:
        """Return how many times the engine tried this backlog task today."""
        state = self.snapshot()
        return int(state.get("task_attempts", {}).get(task_key, 0))

    def record_llm_call(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> dict[str, Any]:
        """Increment usage counters for one provider call."""
        with _STATE_LOCK:
            state = self._load_unlocked()
            provider_key = (provider or "unknown").strip().lower() or "unknown"
            model_key = (model or "unknown").strip() or "unknown"

            in_tokens = max(0, int(input_tokens or 0))
            out_tokens = max(0, int(output_tokens or 0))

            state["llm_calls"] += 1
            state["input_tokens"] += in_tokens
            state["output_tokens"] += out_tokens

            provider_bucket = state.setdefault("providers", {}).setdefault(
                provider_key,
                {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "models": {}},
            )
            provider_bucket["llm_calls"] += 1
            provider_bucket["input_tokens"] += in_tokens
            provider_bucket["output_tokens"] += out_tokens

            model_bucket = provider_bucket.setdefault("models", {}).setdefault(
                model_key,
                {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0},
            )
            model_bucket["llm_calls"] += 1
            model_bucket["input_tokens"] += in_tokens
            model_bucket["output_tokens"] += out_tokens

            self._write_unlocked(state)
            return json.loads(json.dumps(state))

    def record_proactive_run(
        self,
        *,
        success: bool,
        task_key: str | None = None,
    ) -> dict[str, Any]:
        """Increment daily proactive run counters."""
        with _STATE_LOCK:
            state = self._load_unlocked()
            state["proactive_runs"] += 1
            if not success:
                state["failed_evolutions"] += 1
            if task_key:
                runs_by_task = state.setdefault("proactive_runs_by_task", {})
                runs_by_task[task_key] = int(runs_by_task.get(task_key, 0)) + 1
            self._write_unlocked(state)
            return json.loads(json.dumps(state))

    def record_task_attempt(self, task_key: str) -> dict[str, Any]:
        """Increment the number of starts for a backlog task today."""
        with _STATE_LOCK:
            state = self._load_unlocked()
            attempts = state.setdefault("task_attempts", {})
            attempts[task_key] = int(attempts.get(task_key, 0)) + 1
            self._write_unlocked(state)
            return json.loads(json.dumps(state))

    def _default_state(self) -> dict[str, Any]:
        now = _utcnow().isoformat()
        return {
            "date": _today_key(),
            "updated_at": now,
            "llm_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "proactive_runs": 0,
            "failed_evolutions": 0,
            "task_attempts": {},
            "proactive_runs_by_task": {},
            "providers": {},
        }

    def _load_unlocked(self) -> dict[str, Any]:
        today = _today_key()
        if not self.state_path.exists():
            return self._default_state()

        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return self._default_state()

        if not isinstance(data, dict) or data.get("date") != today:
            return self._default_state()

        merged = self._default_state()
        merged.update(data)
        merged["date"] = today
        return merged

    def _write_unlocked(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        state["date"] = _today_key()
        state["updated_at"] = _utcnow().isoformat()
        tmp_path = self.state_path.with_suffix(f"{self.state_path.suffix}.tmp")
        tmp_path.write_text(
            json.dumps(state, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(self.state_path)
