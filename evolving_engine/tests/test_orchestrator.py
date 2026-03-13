"""Tests for the Orchestrator state machine."""

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
