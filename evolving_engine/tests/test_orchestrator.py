"""Tests for the Orchestrator state machine."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import engine.orchestrator as orchestrator_module
from engine.config import EngineSettings
from engine.context import create_context
from engine.models.backlog import (
    BacklogAppSpec,
    BacklogFeatureSpec,
    BacklogItem,
    BacklogTaskPriority,
    BacklogTaskStatus,
    BacklogTaskType,
)
from engine.models.evolution import EvolutionSource, EvolutionStatus
from engine.monitor.models import ContractProbeFailure, RuntimeSnapshot
from engine.orchestrator import Orchestrator


def test_agent_mapping():
    """All pipeline statuses map to an agent or are terminal."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    # Manually set up agents for testing
    orchestrator.leader = "leader_agent"
    orchestrator.data_manager = "data_manager_agent"
    orchestrator.generator = "generator_agent"
    orchestrator.validator = "validator_agent"

    assert orchestrator._get_agent_for_status(EvolutionStatus.RECEIVED) is not None
    assert orchestrator._get_agent_for_status(EvolutionStatus.ANALYZING) is not None
    assert orchestrator._get_agent_for_status(EvolutionStatus.GENERATING) is not None
    assert orchestrator._get_agent_for_status(EvolutionStatus.VALIDATING) is not None

    # Terminal / deployment states have no agent
    assert orchestrator._get_agent_for_status(EvolutionStatus.DEPLOYING) is None
    assert orchestrator._get_agent_for_status(EvolutionStatus.COMPLETED) is None
    assert orchestrator._get_agent_for_status(EvolutionStatus.FAILED) is None


def test_select_next_backlog_item_prefers_resuming_in_progress_work():
    """The planner resumes an in-progress item before starting a new pending one."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    items = [
        _backlog_item(task_key="foundation", sequence=1, status=BacklogTaskStatus.DONE),
        _backlog_item(
            item_id="2",
            task_key="api_slice",
            sequence=2,
            status=BacklogTaskStatus.IN_PROGRESS,
            depends_on=["foundation"],
        ),
        _backlog_item(
            item_id="3",
            task_key="ui_slice",
            sequence=3,
            status=BacklogTaskStatus.PENDING,
            depends_on=["api_slice"],
        ),
    ]

    selected = orchestrator._select_next_backlog_item(items)

    assert selected is not None
    assert selected.task_key == "api_slice"


def test_select_next_backlog_item_skips_unsatisfied_dependencies():
    """Pending tasks are skipped until all depends_on task_keys are done."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    items = [
        _backlog_item(item_id="1", task_key="foundation", sequence=1, status=BacklogTaskStatus.PENDING),
        _backlog_item(
            item_id="2",
            task_key="ui_slice",
            sequence=2,
            status=BacklogTaskStatus.PENDING,
            depends_on=["foundation"],
        ),
    ]

    selected = orchestrator._select_next_backlog_item(items)

    assert selected is not None
    assert selected.task_key == "foundation"


def test_select_next_backlog_item_prefers_high_priority_ready_work():
    """Pending work should prefer higher-priority ready items over lower-priority siblings."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    items = [
        _backlog_item(item_id="1", task_key="export_csv", sequence=1, priority=BacklogTaskPriority.LOW),
        _backlog_item(item_id="2", task_key="repair_search", sequence=2, priority=BacklogTaskPriority.HIGH),
    ]

    selected = orchestrator._select_next_backlog_item(items)

    assert selected is not None
    assert selected.task_key == "repair_search"


def test_select_next_backlog_item_skips_retry_cooldown_and_keeps_moving():
    """A cooling task should not block another ready task from running."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    items = [
        _backlog_item(
            item_id="1",
            task_key="repair_search",
            sequence=1,
            priority=BacklogTaskPriority.HIGH,
            retry_after=datetime.now(timezone.utc) + timedelta(minutes=5),
        ),
        _backlog_item(
            item_id="2",
            task_key="company_export",
            sequence=2,
            priority=BacklogTaskPriority.NORMAL,
        ),
    ]

    selected = orchestrator._select_next_backlog_item(items)

    assert selected is not None
    assert selected.task_key == "company_export"


def test_select_next_backlog_item_skips_task_that_hit_daily_attempt_cap():
    """A task that already burned today's attempt budget should not monopolize the backlog."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.daily_task_attempt_limit = 3
    orchestrator.usage_tracker = _UsageTrackerStub({"repair_search": 3})
    items = [
        _backlog_item(
            item_id="1",
            task_key="repair_search",
            sequence=1,
            priority=BacklogTaskPriority.HIGH,
        ),
        _backlog_item(
            item_id="2",
            task_key="company_export",
            sequence=2,
            priority=BacklogTaskPriority.NORMAL,
        ),
    ]

    selected = orchestrator._select_next_backlog_item(items)

    assert selected is not None
    assert selected.task_key == "company_export"


def test_inspect_backlog_items_marks_blocked_frontier_when_dependencies_are_blocked():
    """A blocked task should surface as backlog stall pressure for replanning."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    items = [
        _backlog_item(
            item_id="1",
            task_key="timeline_build",
            sequence=1,
            status=BacklogTaskStatus.BLOCKED,
        ),
        _backlog_item(
            item_id="2",
            task_key="company_export",
            sequence=2,
            status=BacklogTaskStatus.PENDING,
            depends_on=["timeline_build"],
        ),
    ]

    state = orchestrator._inspect_backlog_items(items)

    assert state.actionable_item is None
    assert state.blocked_frontier_item is not None
    assert state.blocked_frontier_item.task_key == "timeline_build"
    assert state.is_stalled is True


def test_backlog_replan_reason_is_none_for_retry_cooldown_only():
    """Cooling tasks should wait, not force an immediate roadmap rewrite."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    items = [
        _backlog_item(
            item_id="1",
            task_key="timeline_build",
            sequence=1,
            retry_after=datetime.now(timezone.utc) + timedelta(minutes=5),
        ),
    ]

    assert orchestrator._backlog_replan_reason(items) is None


def test_backlog_replan_reason_reports_blocked_frontier():
    """Blocked backlog fronts should force a replan instead of an idle wait."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    items = [
        _backlog_item(
            item_id="1",
            task_key="timeline_build",
            sequence=1,
            status=BacklogTaskStatus.BLOCKED,
        ),
    ]

    assert orchestrator._backlog_replan_reason(items) == "blocked_frontier:timeline_build:blocked"


def test_proactive_budget_status_blocks_when_daily_llm_calls_are_exhausted():
    """Daily budgets should put proactive work into safe mode before another loop burns cost."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.daily_llm_calls_limit = 5
    orchestrator.daily_input_tokens_limit = 500_000
    orchestrator.daily_output_tokens_limit = 120_000
    orchestrator.daily_proactive_runs_limit = 24
    orchestrator.daily_failed_evolutions_limit = 10
    orchestrator.usage_tracker = _UsageTrackerSnapshotStub(
        {
            "llm_calls": 5,
            "input_tokens": 1200,
            "output_tokens": 300,
            "proactive_runs": 2,
            "failed_evolutions": 0,
        }
    )

    allowed, reason, snapshot = orchestrator._proactive_budget_status()

    assert allowed is False
    assert reason == "daily_llm_calls_limit"
    assert snapshot["llm_calls"] == 5


def test_build_request_from_anomaly_tolerates_string_latency_values():
    """Runtime anomaly evidence can arrive as strings and must not crash the loop."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.config = EngineSettings(monitor_latency_threshold_ms=800.0)
    anomaly = SimpleNamespace(
        type=orchestrator_module.AnomalyType.HIGH_LATENCY,
        evidence={"method": "GET", "path": "/api/v1/apps", "avg_latency_ms": "950"},
        description="Slow endpoint",
    )
    snapshot = SimpleNamespace(
        recent_errors=[],
        global_error_rate=0.0,
        total_errors=0,
        total_requests=10,
    )

    request = orchestrator._build_request_from_anomaly(anomaly, snapshot)

    assert "950ms average latency" in request


@pytest.mark.asyncio
async def test_peek_actionable_backlog_item_uses_completed_dependencies():
    """Backlog probing should consider done tasks so dependent pending work can resume."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.purpose = SimpleNamespace(version=2)
    reporter = _BacklogReporter(
        [
            _backlog_item(item_id="1", task_key="foundation", sequence=1, status=BacklogTaskStatus.DONE),
            _backlog_item(
                item_id="2",
                task_key="timeline_stub",
                sequence=2,
                status=BacklogTaskStatus.PENDING,
                depends_on=["foundation"],
            ),
        ]
    )
    orchestrator.event_reporter = reporter

    selected = await orchestrator._peek_actionable_backlog_item()

    assert reporter.fetch_calls == [{"purpose_version": 2, "include_completed": True}]
    assert selected is not None
    assert selected.task_key == "timeline_stub"


@pytest.mark.asyncio
async def test_peek_actionable_backlog_item_skips_blocked_only_backlog():
    """Blocked-only backlogs should not trigger another proactive run."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.purpose = SimpleNamespace(version=2)
    orchestrator.event_reporter = _BacklogReporter(
        [
            _backlog_item(
                item_id="2",
                task_key="timeline_stub",
                sequence=2,
                status=BacklogTaskStatus.BLOCKED,
                depends_on=["foundation"],
            ),
        ]
    )

    selected = await orchestrator._peek_actionable_backlog_item()

    assert selected is None


def test_finalize_backlog_item_blocks_after_third_failed_attempt():
    """Repeated failures move the backlog item to blocked with the captured error."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    reporter = _RecordingReporter()
    orchestrator.event_reporter = reporter

    item = _backlog_item(item_id="2", task_key="api_slice", attempt_count=2, failure_streak=2)
    ctx = create_context("Build company search")
    ctx = ctx.fail("relation companies does not exist")

    asyncio.run(orchestrator._finalize_backlog_item(item, ctx))

    item_id, payload = reporter.updates[0]
    assert item_id == "2"
    assert payload["status"] == BacklogTaskStatus.BLOCKED.value
    assert payload["last_request_id"] == ctx.request_id
    assert payload["attempt_count"] == 3
    assert payload["failure_streak"] == 3
    assert payload["last_error"] == "relation companies does not exist"
    assert payload["blocked_reason"] == "relation companies does not exist"
    assert payload["retry_after"] is None
    assert payload["started_at"] is None
    assert payload["completed_at"] is None


def test_finalize_backlog_item_adds_retry_cooldown_for_structural_failure():
    """A first code failure should stay pending with a retry_after backoff."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    reporter = _RecordingReporter()
    orchestrator.event_reporter = reporter

    item = _backlog_item(item_id="7", task_key="api_slice")
    ctx = create_context("Build company search")
    ctx = ctx.fail("validation failed: endpoint contract mismatch")

    asyncio.run(orchestrator._finalize_backlog_item(item, ctx))

    item_id, payload = reporter.updates[0]
    assert item_id == "7"
    assert payload["status"] == BacklogTaskStatus.PENDING.value
    assert payload["attempt_count"] == 1
    assert payload["failure_streak"] == 1
    assert payload["blocked_reason"] is None
    assert payload["retry_after"] is not None


def test_finalize_backlog_item_keeps_transient_failure_pending():
    """Transient infra errors should cool down instead of immediately increasing structural debt."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    reporter = _RecordingReporter()
    orchestrator.event_reporter = reporter

    item = _backlog_item(item_id="8", task_key="api_slice", attempt_count=2, failure_streak=1)
    ctx = create_context("Build company search")
    ctx = ctx.fail("503 Service Unavailable from provider")

    asyncio.run(orchestrator._finalize_backlog_item(item, ctx))

    item_id, payload = reporter.updates[0]
    assert item_id == "8"
    assert payload["status"] == BacklogTaskStatus.PENDING.value
    assert payload["attempt_count"] == 3
    assert payload["failure_streak"] == 0
    assert payload["blocked_reason"] is None
    assert payload["retry_after"] is not None


def test_finalize_backlog_item_marks_success_done():
    """Successful executions mark the backlog item as done."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    reporter = _RecordingReporter()
    orchestrator.event_reporter = reporter

    item = _backlog_item(item_id="9", task_key="foundation", attempt_count=1)
    ctx = create_context("Build company search")
    ctx = ctx.transition(EvolutionStatus.COMPLETED)

    asyncio.run(orchestrator._finalize_backlog_item(item, ctx))

    item_id, payload = reporter.updates[0]
    assert item_id == "9"
    assert payload["status"] == BacklogTaskStatus.DONE.value
    assert payload["last_request_id"] == ctx.request_id
    assert payload["attempt_count"] == 2
    assert payload["failure_streak"] == 0
    assert payload["last_error"] is None
    assert payload["blocked_reason"] is None
    assert payload["retry_after"] is None
    assert "completed_at" in payload


@pytest.mark.asyncio
async def test_recover_stale_backlog_items_requeues_abandoned_in_progress_work():
    """Stale in-progress items should be released back to pending with a cooldown."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    reporter = _RecordingReporter()
    orchestrator.event_reporter = reporter
    stale_started_at = datetime.now(timezone.utc) - timedelta(hours=2)

    recovered = await orchestrator._recover_stale_backlog_items(
        [
            _backlog_item(
                item_id="11",
                task_key="timeline_stub",
                status=BacklogTaskStatus.IN_PROGRESS,
                attempt_count=2,
                failure_streak=1,
                started_at=stale_started_at,
            )
        ]
    )

    assert recovered is True
    item_id, payload = reporter.updates[0]
    assert item_id == "11"
    assert payload["status"] == BacklogTaskStatus.PENDING.value
    assert payload["attempt_count"] == 2
    assert payload["failure_streak"] == 1
    assert payload["blocked_reason"] is None
    assert payload["retry_after"] is not None
    assert payload["started_at"] is None


def test_ensure_app_registered_sets_frontend_entry_metadata():
    """New app shells get a stable frontend entry derived from the app name."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    reporter = _AppRegistrationReporter()
    orchestrator.event_reporter = reporter

    app_id = asyncio.run(
        orchestrator._ensure_app_registered(
            BacklogAppSpec(
                name="Competitive Intelligence",
                icon="🔎",
                goal="Research competitor companies",
                features=[BacklogFeatureSpec(name="Company Discovery", description="Launch search UI")],
            )
        )
    )

    assert app_id == "app-123"
    assert reporter.create_app_payloads == [
        {
            "name": "Competitive Intelligence",
            "icon": "🔎",
            "goal": "Research competitor companies",
            "status": "building",
            "features": [
                {
                    "name": "Company Discovery",
                    "description": "Launch search UI",
                    "user_facing_description": "Launch search UI",
                    "capability_ids": [],
                }
            ],
            "capability_ids": [],
            "metadata_json": {"frontend_entry": "competitive-intelligence"},
        }
    ]


def test_build_provider_supports_openai(monkeypatch):
    """The provider factory should instantiate OpenAI when configured."""

    class DummyOpenAIProvider:
        def __init__(self, config):
            self.config = config

    monkeypatch.setattr(orchestrator_module, "OpenAIProvider", DummyOpenAIProvider)

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.config = EngineSettings(
        llm_provider="openai",
        openai_api_key="test-key",
        openai_model="gpt-5.2",
    )

    provider = orchestrator._build_provider()

    assert isinstance(provider, DummyOpenAIProvider)
    assert provider.config.openai_model == "gpt-5.2"


@pytest.mark.asyncio
async def test_refresh_runtime_llm_config_switches_provider_and_model(monkeypatch):
    """Engine-scoped runtime settings should switch provider/model without restart."""

    class DummyOpenAIProvider:
        def __init__(self, config):
            self.config = config

    monkeypatch.setattr(orchestrator_module, "OpenAIProvider", DummyOpenAIProvider)

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.config = EngineSettings(
        llm_provider="anthropic",
        anthropic_api_key="anthropic-key",
        anthropic_model="claude-sonnet-4-20250514",
    )
    orchestrator._provider_managed_externally = False
    orchestrator.provider = SimpleNamespace(name="old-provider")
    orchestrator.leader = SimpleNamespace(provider=None)
    orchestrator.generator = SimpleNamespace(provider=None)
    orchestrator.purpose_evolver = SimpleNamespace(provider=None)
    orchestrator.event_reporter = _SettingsReporter(
        {
            "chat_llm_provider": "anthropic",
            "chat_llm_model": "claude-sonnet-4-20250514",
            "engine_llm_provider": "openai",
            "engine_llm_model": "gpt-5.3-codex",
            "openai_api_key": "openai-key",
        }
    )
    orchestrator._last_llm_config_signature = orchestrator._current_llm_signature()

    await orchestrator._refresh_runtime_llm_config()

    assert orchestrator.config.llm_provider == "openai"
    assert orchestrator.config.openai_model == "gpt-5.3-codex"
    assert orchestrator.config.openai_model_fast == "gpt-5.3-codex"
    assert isinstance(orchestrator.provider, DummyOpenAIProvider)
    assert orchestrator.leader.provider is orchestrator.provider
    assert orchestrator.generator.provider is orchestrator.provider
    assert orchestrator.purpose_evolver.provider is orchestrator.provider


@pytest.mark.asyncio
async def test_refresh_runtime_llm_config_falls_back_to_legacy_shared_settings(monkeypatch):
    """Legacy llm_provider/llm_model should remain a valid fallback."""

    class DummyOpenAIProvider:
        def __init__(self, config):
            self.config = config

    monkeypatch.setattr(orchestrator_module, "OpenAIProvider", DummyOpenAIProvider)

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.config = EngineSettings(
        llm_provider="anthropic",
        anthropic_api_key="anthropic-key",
        anthropic_model="claude-sonnet-4-20250514",
    )
    orchestrator._provider_managed_externally = False
    orchestrator.provider = SimpleNamespace(name="old-provider")
    orchestrator.leader = SimpleNamespace(provider=None)
    orchestrator.generator = SimpleNamespace(provider=None)
    orchestrator.purpose_evolver = SimpleNamespace(provider=None)
    orchestrator.event_reporter = _SettingsReporter(
        {
            "llm_provider": "openai",
            "llm_model": "gpt-5.2",
            "openai_api_key": "openai-key",
        }
    )
    orchestrator._last_llm_config_signature = orchestrator._current_llm_signature()

    await orchestrator._refresh_runtime_llm_config()

    assert orchestrator.config.llm_provider == "openai"
    assert orchestrator.config.openai_model == "gpt-5.2"
    assert isinstance(orchestrator.provider, DummyOpenAIProvider)


def test_collect_runtime_incident_lessons_translates_structural_signals():
    """Instance incidents should become reusable framework hardening lessons."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    snapshot = RuntimeSnapshot(
        contract_failures=[
            ContractProbeFailure(
                app_key="framework",
                method="POST",
                path="/api/v1/chat",
                description="Chat route",
                expected_statuses=[200],
                status_code=404,
                detail="Not Found",
            )
        ],
        recent_errors=[
            {
                "path": "/api/v1/settings/engine_llm_provider",
                "status_code": 401,
                "detail": "Unauthorized",
            },
            {
                "exception": "UndefinedColumn: column system_settings.is_secret does not exist",
            },
            {
                "exception": (
                    "AccessDeniedException: explicit deny on bedrock:InvokeModel after "
                    "fallback to llm_provider=bedrock"
                ),
            },
        ],
    )
    ctx = create_context(
        "Repair live instance runtime incident",
        source=EvolutionSource.MONITOR,
        runtime_snapshot=snapshot,
    ).transition(EvolutionStatus.COMPLETED)

    lessons = orchestrator._collect_runtime_incident_lessons(ctx)
    titles = {lesson.title for lesson in lessons}

    assert "Instance incidents must harden the open-source framework" in titles
    assert "Promote critical instance routes into explicit runtime contracts" in titles
    assert "Engine runtime settings endpoints must remain engine-readable" in titles
    assert "Control-plane settings reads must tolerate schema drift" in titles
    assert "Provider selection must fail closed when runtime config is unavailable" in titles


@pytest.mark.asyncio
async def test_extract_lessons_from_runtime_incident_reuses_memory_store():
    """Runtime incident lessons should go through idempotent memory persistence."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    reporter = _LessonReporter()
    orchestrator.event_reporter = reporter
    snapshot = RuntimeSnapshot(
        contract_failures=[
            ContractProbeFailure(
                app_key="framework",
                method="POST",
                path="/api/v1/chat",
                description="Chat route",
                expected_statuses=[200],
                status_code=404,
                detail="Not Found",
            )
        ]
    )
    ctx = create_context(
        "Repair missing chat route",
        source=EvolutionSource.MONITOR,
        runtime_snapshot=snapshot,
    ).transition(EvolutionStatus.COMPLETED)

    await orchestrator._extract_lessons_from_runtime_incident(ctx)

    titles = [payload["title"] for payload in reporter.lessons]
    assert "Instance incidents must harden the open-source framework" in titles
    assert "Promote critical instance routes into explicit runtime contracts" in titles


class _RecordingReporter:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict]] = []

    async def update_backlog_item(self, item_id: str, payload: dict):
        self.updates.append((item_id, payload))
        return None


class _AppRegistrationReporter:
    def __init__(self) -> None:
        self.create_app_payloads: list[dict] = []

    async def fetch_apps(self):
        return []

    async def create_capability(self, payload: dict):
        raise AssertionError(f"Unexpected capability creation: {payload}")

    async def create_app(self, payload: dict):
        self.create_app_payloads.append(payload)
        return "app-123"


class _SettingsReporter:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    async def get_setting(self, key: str):
        return self.values.get(key)


class _LessonReporter:
    def __init__(self) -> None:
        self.lessons: list[dict[str, str]] = []

    async def remember_lesson(self, **payload: str):
        self.lessons.append(payload)
        return f"lesson-{len(self.lessons)}"


class _UsageTrackerStub:
    def __init__(self, attempts: dict[str, int]) -> None:
        self.attempts = attempts

    def task_attempts_today(self, task_key: str) -> int:
        return self.attempts.get(task_key, 0)


class _UsageTrackerSnapshotStub(_UsageTrackerStub):
    def __init__(self, snapshot: dict[str, int]) -> None:
        super().__init__({})
        self._snapshot = snapshot

    def snapshot(self) -> dict[str, int]:
        return dict(self._snapshot)


class _BacklogReporter:
    def __init__(self, items: list[BacklogItem] | None) -> None:
        self.items = items
        self.fetch_calls: list[dict[str, object]] = []

    async def fetch_backlog(self, purpose_version: int | None = None, include_completed: bool = True):
        self.fetch_calls.append(
            {
                "purpose_version": purpose_version,
                "include_completed": include_completed,
            }
        )
        return self.items


def _backlog_item(
    *,
    item_id: str = "1",
    task_key: str,
    sequence: int = 1,
    status: BacklogTaskStatus = BacklogTaskStatus.PENDING,
    priority: BacklogTaskPriority = BacklogTaskPriority.NORMAL,
    depends_on: list[str] | None = None,
    attempt_count: int = 0,
    failure_streak: int = 0,
    retry_after: datetime | None = None,
    started_at: datetime | None = None,
) -> BacklogItem:
    return BacklogItem(
        id=item_id,
        purpose_version=2,
        task_key=task_key,
        title=task_key.replace("_", " ").title(),
        description="",
        status=status,
        priority=priority,
        sequence=sequence,
        task_type=BacklogTaskType.EVOLVE,
        execution_request="Build the next slice",
        acceptance_criteria=[],
        depends_on=depends_on or [],
        attempt_count=attempt_count,
        failure_streak=failure_streak,
        retry_after=retry_after,
        started_at=started_at,
    )
