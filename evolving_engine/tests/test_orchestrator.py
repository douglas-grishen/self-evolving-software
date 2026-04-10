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
from engine.models.inception import InceptionRequest
from engine.models.evolution import EvolutionStatus
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


def test_build_backlog_stability_constraints_flags_frontend_build_failures():
    """Repeated frontend build failures should narrow future backlog planning."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    items = [
        _backlog_item(
            item_id="1",
            task_key="stabilize_ai_projects_hub_frontend_contract",
            status=BacklogTaskStatus.BLOCKED,
            failure_streak=3,
            blocked_reason=(
                "Currently blocked by repeated failed execution attempts with frontend "
                "Docker build errors; next retry should remain a minimal frontend-only repair."
            ),
            last_error=(
                "Docker build failed for frontend: The command '/bin/sh -c npm run build' "
                "returned a non-zero code: 1"
            ),
        )
    ]

    constraints = orchestrator._build_backlog_stability_constraints(items)

    assert "minimal frontend-only stabilization task" in constraints
    assert "avoid pairing UI work with schema, API, or observability changes" in constraints
    assert "`stabilize_ai_projects_hub_frontend_contract` is the blocked frontier" in constraints


def test_build_backlog_stability_constraints_flags_discovery_schema_drift():
    """Discovery migration failures should push the planner toward schema-first hardening."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    items = [
        _backlog_item(
            item_id="1",
            task_key="add_configurable_github_search_criteria_observability",
            failure_streak=1,
            last_error=(
                "Generated output does not cover all planned files: "
                "backend/alembic/versions/<next_head>_github_run_criteria_obs.py\n"
                "Alembic revisions must have exactly one head before deploy."
            ),
        )
    ]

    constraints = orchestrator._build_backlog_stability_constraints(items)

    assert "Discovery schema drift is active" in constraints
    assert "Do not plan a single task that mixes discovery migrations with frontend/UI changes" in constraints


def test_build_backlog_stability_constraints_rejects_version_suffix_retry_churn():
    """Repeated blocked siblings should stop the planner from minting v2/v3 task keys."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    items = [
        _backlog_item(
            item_id="1",
            task_key="stabilize_ai_projects_hub_frontend_contract_minimal_v3",
            status=BacklogTaskStatus.BLOCKED,
            blocked_reason="frontend build still failing",
        ),
        _backlog_item(
            item_id="2",
            task_key="stabilize_ai_projects_hub_frontend_contract_minimal_v2",
            status=BacklogTaskStatus.ABANDONED,
            blocked_reason="Removed from replanned backlog",
        ),
        _backlog_item(
            item_id="3",
            task_key="stabilize_ai_projects_hub_frontend_contract_minimal",
            status=BacklogTaskStatus.ABANDONED,
            blocked_reason="Removed from replanned backlog",
        ),
    ]

    constraints = orchestrator._build_backlog_stability_constraints(items)

    assert "Do not create another version-suffixed retry" in constraints
    assert "stabilize_ai_projects_hub_frontend_contract_minimal" in constraints


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


@pytest.mark.asyncio
async def test_proactive_budget_exhaustion_updates_last_run_to_avoid_minute_loop():
    """Budget exhaustion should throttle proactive retries instead of re-triggering every minute."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator._evolution_semaphore = asyncio.Semaphore(1)
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
    orchestrator._last_proactive_run = 0.0
    published = []
    notifications = []

    async def _publish_usage_snapshot():
        published.append(True)

    async def _post_blocker_notification(message: str, *, severity: str = "high"):
        notifications.append((message, severity))

    orchestrator._publish_usage_snapshot = _publish_usage_snapshot
    orchestrator._post_blocker_notification = _post_blocker_notification

    result = await orchestrator._proactive_evolution()

    assert result is True
    assert orchestrator._last_proactive_run > 0
    assert published == [True]
    assert notifications


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


def test_build_request_from_missing_endpoint_anomaly_mentions_contract_repair():
    """Broken mounted app contracts should produce a concrete repair request."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.config = EngineSettings()
    anomaly = SimpleNamespace(
        type=orchestrator_module.AnomalyType.MISSING_ENDPOINT,
        evidence={"method": "POST", "path": "/api/v1/example-app/items/search"},
        description=(
            "Mounted app contract probe failed for POST "
            "/api/v1/example-app/items/search: HTTP 405"
        ),
    )
    snapshot = SimpleNamespace(
        recent_errors=[],
        global_error_rate=0.0,
        total_errors=0,
        total_requests=2,
    )

    request = orchestrator._build_request_from_anomaly(anomaly, snapshot)

    assert "runtime contract is broken" in request
    assert "valid empty-state response" in request
    assert "/api/v1/example-app/items/search" in request


@pytest.mark.asyncio
async def test_run_fails_fast_when_no_purpose_is_defined():
    """Triggered evolutions must be rejected until the instance has a Purpose."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.purpose = None
    orchestrator.leader = SimpleNamespace(purpose=None)

    events = []

    async def post_event(ctx):
        events.append(ctx)

    async def fetch_purpose():
        return None

    async def execute_context(_ctx):
        raise AssertionError("pipeline should not execute without a purpose")

    orchestrator.event_reporter = SimpleNamespace(post_event=post_event)
    orchestrator._fetch_purpose_from_api = fetch_purpose
    orchestrator._execute_context = execute_context

    ctx = await orchestrator.run("Add a dashboard")

    assert ctx.status == EvolutionStatus.FAILED
    assert "Purpose is not defined" in (ctx.error or "")
    assert len(events) == 1
    assert events[0].status == EvolutionStatus.FAILED


@pytest.mark.asyncio
async def test_run_continuous_waits_idle_until_purpose_exists(monkeypatch):
    """Continuous mode should stay idle instead of monitoring/evolving without a Purpose."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.config = EngineSettings(monitor_interval_seconds=7)
    orchestrator.genesis = None
    orchestrator.purpose = None
    orchestrator.leader = SimpleNamespace(purpose=None)
    orchestrator._purpose_synced = False

    async def is_backend_available():
        return True

    orchestrator.event_reporter = SimpleNamespace(is_backend_available=is_backend_available)

    async def fetch_purpose():
        return None

    async def should_not_run(*args, **kwargs):
        raise AssertionError("engine should remain idle until a purpose exists")

    orchestrator._fetch_purpose_from_api = fetch_purpose
    orchestrator._mape_k_iteration = should_not_run
    orchestrator._refresh_runtime_llm_config = should_not_run
    orchestrator._refresh_runtime_guardrails = should_not_run
    orchestrator._publish_usage_snapshot = should_not_run
    orchestrator._process_pending_inceptions = should_not_run

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        orchestrator._running = False

    monkeypatch.setattr(orchestrator_module.asyncio, "sleep", fake_sleep)

    await orchestrator.run_continuous()

    assert sleep_calls == [7]


@pytest.mark.asyncio
async def test_ensure_active_purpose_persists_api_purpose(tmp_path):
    """Purpose fetched from the API should be saved locally for future archival."""

    saved_paths: list[str] = []

    class PurposeStub:
        version = 2
        identity = SimpleNamespace(name="Competitive Intelligence")

        def save(self, path):
            saved_paths.append(str(path))
            path.write_text("purpose: persisted\n", encoding="utf-8")

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.purpose = None
    orchestrator.leader = SimpleNamespace(purpose=None)
    orchestrator.config = SimpleNamespace(purpose_path=tmp_path / "purpose.yaml")

    async def fetch_purpose():
        return PurposeStub()

    orchestrator._fetch_purpose_from_api = fetch_purpose

    loaded = await orchestrator._ensure_active_purpose()

    assert loaded is True
    assert saved_paths == [str(tmp_path / "purpose.yaml")]
    assert orchestrator.leader.purpose is orchestrator.purpose
    assert (tmp_path / "purpose.yaml").read_text(encoding="utf-8") == "purpose: persisted\n"


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


@pytest.mark.asyncio
async def test_recover_resolved_blocked_backlog_items_requeues_contract_blocked_work():
    """Resolved contract blockers should automatically reopen stalled roadmap items."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.event_reporter = _RecordingReporter()
    orchestrator.config = EngineSettings(
        monitor_url="http://backend:8000",
        evolved_app_path="/tmp/evolved-app",
    )
    orchestrator._platform_contract_recovery_errors = lambda: []

    async def no_runtime_errors():
        return []

    orchestrator._runtime_contract_recovery_errors = no_runtime_errors

    recovered = await orchestrator._recover_resolved_blocked_backlog_items(
        [
            _backlog_item(
                item_id="12",
                task_key="contract_fix",
                status=BacklogTaskStatus.BLOCKED,
                failure_streak=3,
                blocked_reason=(
                    "Currently blocked by repeated deployment/runtime smoke-check failure: "
                    "GET /api/v1/competitive-intelligence/statistics returns HTTP 500 after restart."
                ),
            ),
            _backlog_item(
                item_id="13",
                task_key="provider_blip",
                status=BacklogTaskStatus.BLOCKED,
                failure_streak=2,
                blocked_reason="503 Service Unavailable from provider",
            ),
        ]
    )

    assert recovered is True
    assert orchestrator.event_reporter.updates == [
        (
            "12",
            {
                "status": BacklogTaskStatus.PENDING.value,
                "failure_streak": 0,
                "blocked_reason": None,
                "retry_after": None,
                "started_at": None,
                "completed_at": None,
            },
        )
    ]


@pytest.mark.asyncio
async def test_recover_resolved_blocked_backlog_items_waits_until_contracts_are_healthy():
    """Blocked items stay blocked while live contract probes still fail."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    reporter = _RecordingReporter()
    orchestrator.event_reporter = reporter
    orchestrator.config = EngineSettings(
        monitor_url="http://backend:8000",
        evolved_app_path="/tmp/evolved-app",
    )
    orchestrator._platform_contract_recovery_errors = lambda: ["backend/app/api/v1/competitive_intelligence.py missing required markers: @router.get(\"/statistics\")"]

    async def runtime_errors():
        return ["GET /api/v1/competitive-intelligence/statistics -> HTTP 500"]

    orchestrator._runtime_contract_recovery_errors = runtime_errors

    recovered = await orchestrator._recover_resolved_blocked_backlog_items(
        [
            _backlog_item(
                item_id="14",
                task_key="contract_fix",
                status=BacklogTaskStatus.BLOCKED,
                failure_streak=3,
                blocked_reason="Platform contract violation: missing required markers",
            )
        ]
    )

    assert recovered is False
    assert reporter.updates == []


def test_ensure_app_registered_sets_frontend_entry_metadata():
    """New app shells get a stable frontend entry derived from the app name."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    reporter = _AppRegistrationReporter()
    orchestrator.event_reporter = reporter

    app_id = asyncio.run(
        orchestrator._ensure_app_registered(
            BacklogAppSpec(
                name="Example App",
                icon="🔎",
                goal="Provide a starter product surface",
                features=[BacklogFeatureSpec(name="Starter Search", description="Launch search UI")],
            )
        )
    )

    assert app_id == "app-123"
    assert reporter.create_app_payloads == [
        {
            "name": "Example App",
            "icon": "🔎",
            "goal": "Provide a starter product surface",
            "status": "building",
            "features": [
                {
                    "name": "Starter Search",
                    "description": "Launch search UI",
                    "user_facing_description": "Launch search UI",
                    "capability_ids": [],
                }
            ],
            "capability_ids": [],
            "metadata_json": {"frontend_entry": "example-app"},
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
    orchestrator.usage_tracker = SimpleNamespace(
        sync_llm_config_signature=lambda *args, **kwargs: ({}, False)
    )
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
    orchestrator.usage_tracker = SimpleNamespace(
        sync_llm_config_signature=lambda *args, **kwargs: ({}, False)
    )
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


@pytest.mark.asyncio
async def test_process_pending_inceptions_rejects_unexpected_processing_errors():
    """Unexpected inception exceptions should be persisted as a rejection."""

    class RaisingPurposeEvolver:
        async def evolve(self, current_purpose, inception):
            raise RuntimeError("LLM provider offline")

    class InceptionReporter:
        def __init__(self) -> None:
            self.reported: list[tuple[str, object, bool]] = []

        async def poll_inceptions(self):
            return [
                InceptionRequest(
                    id="inc-1",
                    source="human",
                    directive="Tighten contract safety",
                    rationale="Repeated drift needs a Purpose change",
                    status="pending",
                )
            ]

        async def report_inception_result(self, inception_id: str, result, accepted: bool):
            self.reported.append((inception_id, result, accepted))

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.purpose = SimpleNamespace(version=2)
    orchestrator.purpose_evolver = RaisingPurposeEvolver()
    orchestrator.event_reporter = InceptionReporter()

    notifications: list[tuple[str, str]] = []

    async def record_notification(message: str, *, severity: str = "high") -> None:
        notifications.append((message, severity))

    orchestrator._post_blocker_notification = record_notification

    await orchestrator._process_pending_inceptions()

    assert len(orchestrator.event_reporter.reported) == 1
    inception_id, result, accepted = orchestrator.event_reporter.reported[0]
    assert inception_id == "inc-1"
    assert accepted is False
    assert result.previous_purpose_version == 2
    assert result.new_purpose_version == 2
    assert "RuntimeError: LLM provider offline" in result.changes_summary
    assert notifications
    assert notifications[0][1] == "high"
    assert "inc-1" in notifications[0][0]


@pytest.mark.asyncio
async def test_process_pending_inceptions_survives_failure_reporting_errors():
    """A failed rejection write should be logged and not crash the loop."""

    class RaisingPurposeEvolver:
        async def evolve(self, current_purpose, inception):
            raise RuntimeError("parse failure")

    class InceptionReporter:
        async def poll_inceptions(self):
            return [
                InceptionRequest(
                    id="inc-2",
                    source="human",
                    directive="Update Purpose",
                    rationale="",
                    status="pending",
                )
            ]

        async def report_inception_result(self, inception_id: str, result, accepted: bool):
            raise RuntimeError("backend write failed")

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.purpose = SimpleNamespace(version=4)
    orchestrator.purpose_evolver = RaisingPurposeEvolver()
    orchestrator.event_reporter = InceptionReporter()

    notification_calls = 0

    async def record_notification(message: str, *, severity: str = "high") -> None:
        nonlocal notification_calls
        notification_calls += 1

    orchestrator._post_blocker_notification = record_notification

    await orchestrator._process_pending_inceptions()

    assert notification_calls == 0


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
    blocked_reason: str | None = None,
    last_error: str | None = None,
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
        blocked_reason=blocked_reason,
        last_error=last_error,
        retry_after=retry_after,
        started_at=started_at,
    )
