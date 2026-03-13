"""BaseAgent — abstract base class for all evolution agents."""

import time
from abc import ABC, abstractmethod

import structlog

from engine.config import EngineSettings, settings
from engine.context import EvolutionContext


class BaseAgent(ABC):
    """Base class for all agents in the evolution pipeline.

    Each agent:
    - Receives an EvolutionContext
    - Performs its specialized task
    - Returns an updated EvolutionContext with audit events appended
    """

    def __init__(self, config: EngineSettings | None = None) -> None:
        self.config = config or settings
        self.logger = structlog.get_logger().bind(agent=self.name)

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this agent."""

    @abstractmethod
    async def _execute(self, ctx: EvolutionContext) -> EvolutionContext:
        """Internal execution logic — implemented by each agent subclass."""

    async def execute(self, ctx: EvolutionContext) -> EvolutionContext:
        """Execute the agent with logging and error handling.

        Wraps _execute() with:
        - Start/complete/failed audit events
        - Duration measurement
        - Structured logging
        - Exception handling
        """
        self.logger.info("agent.start", request_id=ctx.request_id)
        ctx = ctx.add_event(self.name, "execute", "started")
        start = time.monotonic()

        try:
            ctx = await self._execute(ctx)
            duration_ms = int((time.monotonic() - start) * 1000)
            self.logger.info(
                "agent.complete",
                request_id=ctx.request_id,
                duration_ms=duration_ms,
            )
            ctx = ctx.add_event(
                self.name, "execute", "completed", f"Duration: {duration_ms}ms"
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            self.logger.error(
                "agent.failed",
                request_id=ctx.request_id,
                error=str(exc),
                duration_ms=duration_ms,
            )
            ctx = ctx.add_event(self.name, "execute", "failed", str(exc))
            ctx = ctx.fail(f"[{self.name}] {exc}")

        return ctx
