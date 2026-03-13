"""Tests for EvolutionContext state management."""

from engine.context import EvolutionContext, create_context
from engine.models.evolution import EvolutionStatus


def test_create_context():
    """Factory function creates a valid context."""
    ctx = create_context("Add a products table")
    assert ctx.status == EvolutionStatus.RECEIVED
    assert ctx.request.user_request == "Add a products table"
    assert ctx.request_id is not None
    assert len(ctx.history) == 0


def test_transition():
    """Status transitions produce a new context."""
    ctx = create_context("Test request")
    new_ctx = ctx.transition(EvolutionStatus.ANALYZING)
    assert new_ctx.status == EvolutionStatus.ANALYZING
    assert ctx.status == EvolutionStatus.RECEIVED  # Original unchanged


def test_add_event():
    """Events are appended immutably."""
    ctx = create_context("Test request")
    new_ctx = ctx.add_event("leader", "execute", "started", "details here")
    assert len(new_ctx.history) == 1
    assert new_ctx.history[0].agent == "leader"
    assert len(ctx.history) == 0  # Original unchanged


def test_fail():
    """Failing sets status and error message."""
    ctx = create_context("Test request")
    failed_ctx = ctx.fail("Something went wrong")
    assert failed_ctx.status == EvolutionStatus.FAILED
    assert failed_ctx.error == "Something went wrong"


def test_retry_logic():
    """Retry counter and can_retry work correctly."""
    ctx = create_context("Test request")
    assert ctx.can_retry is True
    assert ctx.retry_count == 0

    ctx = ctx.increment_retry()
    assert ctx.retry_count == 1
    assert ctx.can_retry is True

    ctx = ctx.increment_retry().increment_retry()
    assert ctx.retry_count == 3
    assert ctx.can_retry is False


def test_dry_run():
    """Dry run flag is set correctly."""
    ctx = create_context("Test request", dry_run=True)
    assert ctx.request.dry_run is True
