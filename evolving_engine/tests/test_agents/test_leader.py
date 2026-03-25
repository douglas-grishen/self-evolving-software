"""Tests for the Leader Agent."""

import pytest

from engine.agents.leader import LeaderAgent, _sanitize_plan
from engine.context import create_context
from engine.models.evolution import EvolutionPlan, EvolutionStatus, FileChange
from engine.models.repo_map import FileNode, RepoMap
from engine.providers.base import BaseLLMProvider


class MockProvider(BaseLLMProvider):
    """Mock LLM provider that returns a predefined plan."""

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        **_: object,
    ) -> str:
        return '{"summary": "Add products CRUD", "changes": [], "risk_level": "low"}'

    async def generate_structured(
        self,
        system_prompt,
        user_prompt,
        response_model,
        max_tokens=4096,
        **_: object,
    ):
        if response_model is EvolutionPlan:
            return EvolutionPlan(
                summary="Add products CRUD",
                changes=[],
                risk_level="low",
                reasoning="Simple CRUD operation",
            )
        return await super().generate_structured(
            system_prompt, user_prompt, response_model, max_tokens
        )


@pytest.mark.asyncio
async def test_leader_produces_plan():
    """Leader agent generates an evolution plan from user request."""
    provider = MockProvider()
    leader = LeaderAgent(provider=provider)

    ctx = create_context("Add a products CRUD")
    result = await leader.execute(ctx)

    assert result.plan is not None
    assert result.plan.summary == "Add products CRUD"
    assert result.status == EvolutionStatus.ANALYZING
    assert len(result.history) == 2  # started + completed


@pytest.mark.asyncio
async def test_leader_audit_trail():
    """Leader agent appends audit events."""
    provider = MockProvider()
    leader = LeaderAgent(provider=provider)

    ctx = create_context("Test request")
    result = await leader.execute(ctx)

    assert any(e.agent == "leader" and e.status == "started" for e in result.history)
    assert any(e.agent == "leader" and e.status == "completed" for e in result.history)


def test_sanitize_plan_normalizes_legacy_frontend_paths():
    repo_map = RepoMap(
        tree=FileNode(
            path=".",
            name=".",
            is_dir=True,
            children=[
                FileNode(
                    path="frontend",
                    name="frontend",
                    is_dir=True,
                    children=[
                        FileNode(
                            path="frontend/src",
                            name="src",
                            is_dir=True,
                            children=[
                                FileNode(
                                    path="frontend/src/apps",
                                    name="apps",
                                    is_dir=True,
                                    children=[
                                        FileNode(
                                            path="frontend/src/apps/registry.tsx",
                                            name="registry.tsx",
                                            is_dir=False,
                                        ),
                                        FileNode(
                                            path="frontend/src/apps/competitive-intelligence",
                                            name="competitive-intelligence",
                                            is_dir=True,
                                            children=[
                                                FileNode(
                                                    path="frontend/src/apps/competitive-intelligence/index.tsx",
                                                    name="index.tsx",
                                                    is_dir=False,
                                                )
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
    )
    plan = EvolutionPlan(
        summary="Normalize app roots",
        risk_level="low",
        reasoning="",
        changes=[
            FileChange(
                file_path="frontend/src/config/apps.ts",
                action="modify",
                description="legacy registry update",
                layer="frontend",
            ),
            FileChange(
                file_path="frontend/src/apps/CompetitiveIntelligence/index.tsx",
                action="modify",
                description="camel-case duplicate",
                layer="frontend",
            ),
            FileChange(
                file_path="frontend/src/apps/competitive-intelligence/index.tsx",
                action="modify",
                description="canonical entry",
                layer="frontend",
            ),
        ],
    )

    sanitized = _sanitize_plan(plan, repo_map)
    assert [change.file_path for change in sanitized.changes] == [
        "frontend/src/apps/registry.tsx",
        "frontend/src/apps/competitive-intelligence/index.tsx",
    ]
    assert "camel-case duplicate" in sanitized.changes[1].description
    assert "canonical entry" in sanitized.changes[1].description
