"""Tests for the Orchestrator state machine."""

import asyncio
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

    item = _backlog_item(item_id="2", task_key="api_slice", attempt_count=2)
    ctx = create_context("Build company search")
    ctx = ctx.fail("relation companies does not exist")

    asyncio.run(orchestrator._finalize_backlog_item(item, ctx))

    assert reporter.updates == [
        (
            "2",
            {
                "status": BacklogTaskStatus.BLOCKED.value,
                "last_request_id": ctx.request_id,
                "last_error": "relation companies does not exist",
                "blocked_reason": "relation companies does not exist",
            },
        )
    ]


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
    assert payload["last_error"] is None
    assert payload["blocked_reason"] is None
    assert "completed_at" in payload


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
    depends_on: list[str] | None = None,
    attempt_count: int = 0,
) -> BacklogItem:
    return BacklogItem(
        id=item_id,
        purpose_version=2,
        task_key=task_key,
        title=task_key.replace("_", " ").title(),
        description="",
        status=status,
        priority=BacklogTaskPriority.NORMAL,
        sequence=sequence,
        task_type=BacklogTaskType.EVOLVE,
        execution_request="Build the next slice",
        acceptance_criteria=[],
        depends_on=depends_on or [],
        attempt_count=attempt_count,
    )
