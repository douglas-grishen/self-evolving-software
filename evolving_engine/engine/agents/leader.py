"""Leader Agent — receives user requests and produces an evolution plan.

Responsibilities (MAPE-K: Monitor + Plan):
- Interpret the user's natural language request
- Decide whether the request requires code evolution
- Produce a structured EvolutionPlan with file-level changes
- Orchestrate the overall strategy
"""

from engine.agents.base import BaseAgent
from engine.context import EvolutionContext
from engine.models.evolution import EvolutionPlan, EvolutionStatus
from engine.providers.base import BaseLLMProvider

SYSTEM_PROMPT = """You are the Lead Architect of a self-evolving software system.

Your job is to analyze a user's feature request and produce a structured evolution plan.

The managed application stack:
- Frontend: React + TypeScript (Vite)
- Backend: FastAPI (Python) with SQLAlchemy ORM
- Database: PostgreSQL with Alembic migrations

You receive:
1. The user's request in natural language
2. A repository map showing the current state of the codebase

You must produce a JSON evolution plan specifying:
- summary: a one-line description of the change
- changes: a list of file-level changes (path, action, description, layer)
- requires_migration: whether a new Alembic migration is needed
- requires_new_dependencies: whether new packages must be installed
- risk_level: "low", "medium", or "high"
- reasoning: your thought process

Be precise. Every file that needs to change must be listed. Think step by step."""


class LeaderAgent(BaseAgent):
    """Interprets user requests and produces structured evolution plans."""

    def __init__(self, provider: BaseLLMProvider, **kwargs) -> None:
        super().__init__(**kwargs)
        self.provider = provider

    @property
    def name(self) -> str:
        return "leader"

    async def _execute(self, ctx: EvolutionContext) -> EvolutionContext:
        """Analyze the user request and produce an evolution plan."""
        # Build the user prompt with repo context
        repo_context = ""
        if ctx.repo_map:
            repo_context = f"\n\n## Current Repository State\n{ctx.repo_map.to_context_string()}"

        user_prompt = (
            f"## User Request\n{ctx.request.user_request}"
            f"{repo_context}"
        )

        # Call LLM for structured plan generation
        plan = await self.provider.generate_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=EvolutionPlan,
        )

        self.logger.info(
            "plan.generated",
            summary=plan.summary,
            num_changes=len(plan.changes),
            risk_level=plan.risk_level,
        )

        return ctx.model_copy(
            update={
                "plan": plan,
                "status": EvolutionStatus.ANALYZING,
            }
        )
