"""Data Manager Agent — scans the repository and builds a structural map.

Responsibilities (MAPE-K: Analyze):
- Walk the managed_app/ filesystem
- Build a token-efficient RepoMap (JSON) containing:
  - Directory tree
  - API endpoints
  - Database schema
  - React components
  - Dependencies
- Provide this context to downstream agents so the LLM stays within token limits
"""

from pathlib import Path

from engine.agents.base import BaseAgent
from engine.context import EvolutionContext
from engine.models.evolution import EvolutionStatus
from engine.repo.scanner import build_repo_map


class DataManagerAgent(BaseAgent):
    """Scans the managed application and produces a RepoMap."""

    def __init__(self, managed_app_path: Path | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.managed_app_path = managed_app_path or self.config.managed_app_path

    @property
    def name(self) -> str:
        return "data_manager"

    async def _execute(self, ctx: EvolutionContext) -> EvolutionContext:
        """Scan the repository and attach the RepoMap to the context."""
        app_path = Path(self.managed_app_path).resolve()

        if not app_path.exists():
            return ctx.fail(f"Managed app path does not exist: {app_path}")

        repo_map = build_repo_map(app_path)

        self.logger.info(
            "repo_map.built",
            summary=repo_map.summary,
            context_chars=len(repo_map.to_context_string()),
        )

        return ctx.model_copy(
            update={
                "repo_map": repo_map,
                "status": EvolutionStatus.GENERATING,
            }
        )
