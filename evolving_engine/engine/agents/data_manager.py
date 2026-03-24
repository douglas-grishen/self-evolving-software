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
    """Scans the managed application and produces a RepoMap.

    Also fetches inter-session lessons from the backend memory store so downstream
    agents (especially CodeGeneratorAgent) can avoid repeating past mistakes.
    """

    def __init__(
        self,
        managed_app_path: Path | None = None,
        event_reporter=None,  # EventReporter | None
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.managed_app_path = managed_app_path or self.config.managed_app_path
        self.event_reporter = event_reporter

    @property
    def name(self) -> str:
        return "data_manager"

    def _resolve_app_path(self) -> Path:
        """Prefer the live evolved app when it exists so planning matches deployment."""
        evolved_app_path = Path(self.config.evolved_app_path).resolve()
        if (evolved_app_path / "frontend").exists() and (evolved_app_path / "backend").exists():
            return evolved_app_path
        return Path(self.managed_app_path).resolve()

    async def _execute(self, ctx: EvolutionContext) -> EvolutionContext:
        """Scan the repository and attach the RepoMap + lessons to the context."""
        app_path = self._resolve_app_path()

        if not app_path.exists():
            return ctx.fail(f"Managed app path does not exist: {app_path}")

        repo_map = build_repo_map(app_path)

        self.logger.info(
            "repo_map.built",
            summary=repo_map.summary,
            context_chars=len(repo_map.to_context_string()),
        )

        # Fetch inter-session lessons (fire-and-forget: returns [] if backend unreachable)
        lessons = []
        if self.event_reporter:
            lessons = await self.event_reporter.fetch_lessons()

        return ctx.model_copy(
            update={
                "repo_map": repo_map,
                "lessons": lessons,
                "status": EvolutionStatus.GENERATING,
            }
        )
