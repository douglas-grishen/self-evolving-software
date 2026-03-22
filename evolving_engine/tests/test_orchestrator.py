"""Tests for the Orchestrator state machine."""

import asyncio

from engine.context import create_context
from engine.models.backlog import (
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


class _RecordingReporter:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict]] = []

    async def update_backlog_item(self, item_id: str, payload: dict):
        self.updates.append((item_id, payload))
        return None


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
