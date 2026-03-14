"""Orchestrator — the core of the MAPE-K feedback loop.

Supports two operating modes:

1. TRIGGERED MODE  (run)
   Executes a single evolution cycle driven by a user request.
   Pipeline: RECEIVED → Leader → ANALYZING → DataManager → GENERATING →
             Generator → VALIDATING → Validator → DEPLOYING → Deployer → COMPLETED

2. CONTINUOUS MODE (run_continuous)
   Runs an autonomous loop that periodically polls the Managed System via the
   RuntimeObserver, detects anomalies, converts them into evolution requests,
   and executes the pipeline automatically — without any human trigger.

   Monitor → Analyze anomalies → Plan → Generate → Validate → Deploy → (repeat)

Both modes share the same agent pipeline. The difference is what initiates it
and what the request context contains (user text vs observed anomaly).

SELF-MODIFICATION:
   The engine can evolve both the Managed System (managed_app/) and itself
   (evolving_engine/). When the Leader agent decides that the engine's own code
   needs improvement, the DataManager scans the engine's source instead, and
   the Generator writes changes there. The same Validator and Deployer apply.
"""

import asyncio
import structlog

from engine.agents.base import BaseAgent
from engine.agents.data_manager import DataManagerAgent
from engine.agents.generator import CodeGeneratorAgent
from engine.agents.leader import LeaderAgent
from engine.agents.validator import CodeValidatorAgent
from engine.config import EngineSettings, settings
from engine.context import EvolutionContext, create_context
from engine.deployer.git_ops import LocalDeployer
from engine.models.evolution import DeploymentResult, EvolutionStatus, EvolutionSource
from engine.monitor.models import Anomaly, AnomalyType, RuntimeSnapshot
from engine.monitor.observer import RuntimeObserver
from engine.providers.anthropic_provider import AnthropicProvider
from engine.providers.base import BaseLLMProvider
from engine.providers.bedrock_provider import BedrockProvider
from engine.sandbox.base import BaseSandbox
from engine.sandbox.docker_sandbox import DockerSandbox

logger = structlog.get_logger()

# How many anomaly-driven evolutions can run concurrently (prevent storm)
_MAX_CONCURRENT_EVOLUTIONS = 1


class Orchestrator:
    """Drives the MAPE-K loop through a state machine.

    Each pipeline status maps to an agent. The orchestrator:
      1. Checks the current status
      2. Runs the corresponding agent
      3. Reads the new status from the updated context
      4. Repeats until COMPLETED or FAILED
    """

    def __init__(
        self,
        config: EngineSettings | None = None,
        provider: BaseLLMProvider | None = None,
        sandbox: BaseSandbox | None = None,
    ) -> None:
        self.config = config or settings

        # LLM provider
        if provider:
            self.provider = provider
        elif self.config.llm_provider == "bedrock":
            self.provider = BedrockProvider(self.config)
        else:
            self.provider = AnthropicProvider(self.config)

        # Sandbox
        self.sandbox = sandbox or DockerSandbox(self.config)

        # Agents (shared by both triggered and continuous modes)
        self.leader = LeaderAgent(provider=self.provider, config=self.config)
        self.data_manager = DataManagerAgent(config=self.config)
        self.generator = CodeGeneratorAgent(provider=self.provider, config=self.config)
        self.validator = CodeValidatorAgent(sandbox=self.sandbox, config=self.config)

        # Deployer (local only — never pushes to GitHub)
        self.deployer = LocalDeployer(self.config)

        # Runtime observer (Monitor phase)
        self.observer = RuntimeObserver(
            base_url=self.config.monitor_url,
            error_rate_threshold=self.config.monitor_error_rate_threshold,
            latency_threshold_ms=self.config.monitor_latency_threshold_ms,
            db_latency_threshold_ms=self.config.monitor_db_latency_threshold_ms,
        )

        # Semaphore — prevents concurrent evolution storms
        self._evolution_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_EVOLUTIONS)

        # Runtime state for continuous mode
        self._running = False
        self._evolution_count = 0

    # -----------------------------------------------------------------------
    # Public API — Triggered mode
    # -----------------------------------------------------------------------

    async def run(
        self,
        user_request: str,
        dry_run: bool = False,
        source: EvolutionSource = EvolutionSource.USER,
        runtime_snapshot: RuntimeSnapshot | None = None,
    ) -> EvolutionContext:
        """Execute a single evolution pipeline.

        Args:
            user_request:     Natural language description of the desired change.
            dry_run:          If True, validate but skip deployment.
            source:           Who initiated this evolution (USER or MONITOR).
            runtime_snapshot: Runtime context attached to monitor-triggered runs.

        Returns:
            Final EvolutionContext with results and audit trail.
        """
        ctx = create_context(
            user_request,
            dry_run=dry_run,
            source=source,
            runtime_snapshot=runtime_snapshot,
        )

        logger.info(
            "pipeline.start",
            request_id=ctx.request_id,
            source=source.value,
            request=user_request[:120],
            dry_run=dry_run,
        )

        ctx = await self._run_state_machine(ctx)

        logger.info(
            "pipeline.complete",
            request_id=ctx.request_id,
            status=ctx.status.value,
            retries=ctx.retry_count,
            evolution_count=self._evolution_count,
        )

        self._evolution_count += 1
        return ctx

    # -----------------------------------------------------------------------
    # Public API — Continuous mode (autonomous MAPE-K loop)
    # -----------------------------------------------------------------------

    async def run_continuous(self) -> None:
        """Run the autonomous MAPE-K loop indefinitely.

        The loop:
          1. MONITOR  — observe the Managed System via the control-plane
          2. ANALYZE  — detect anomalies in the snapshot
          3. PLAN     — convert each anomaly into a natural language request
          4. EXECUTE  — run the evolution pipeline for each request
          5. KNOWLEDGE — log outcomes; the next iteration learns from them
          6. Wait for the configured interval, then repeat

        This method runs until cancelled (e.g., via KeyboardInterrupt or
        Docker SIGTERM). It never raises — all errors are logged and the
        loop continues on the next interval.
        """
        self._running = True
        interval = self.config.monitor_interval_seconds

        logger.info(
            "continuous_loop.start",
            monitor_url=self.config.monitor_url,
            interval_seconds=interval,
        )

        while self._running:
            try:
                await self._mape_k_iteration()
            except asyncio.CancelledError:
                logger.info("continuous_loop.cancelled")
                break
            except Exception as exc:
                logger.exception("continuous_loop.iteration_error", error=str(exc))

            logger.debug("continuous_loop.sleeping", seconds=interval)
            await asyncio.sleep(interval)

        self._running = False
        logger.info("continuous_loop.stopped")

    def stop(self) -> None:
        """Signal the continuous loop to stop after the current iteration."""
        self._running = False

    # -----------------------------------------------------------------------
    # Internal — single MAPE-K iteration
    # -----------------------------------------------------------------------

    async def _mape_k_iteration(self) -> None:
        """Execute one full Monitor → Analyze → Plan → Execute cycle."""

        # ── MONITOR ──────────────────────────────────────────────────────────
        logger.info("mape_k.monitor.start")
        snapshot = await self.observer.observe()

        logger.info(
            "mape_k.monitor.complete",
            reachable=snapshot.reachable,
            anomaly_count=len(snapshot.anomalies),
            summary=snapshot.summary(),
        )

        if not snapshot.reachable:
            logger.warning("mape_k.monitor.unreachable — skipping this iteration")
            return

        # ── ANALYZE ──────────────────────────────────────────────────────────
        if not snapshot.has_anomalies:
            logger.info("mape_k.analyze.no_anomalies — system healthy, nothing to evolve")
            return

        logger.info(
            "mape_k.analyze.anomalies_detected",
            count=len(snapshot.anomalies),
            types=[a.type.value for a in snapshot.anomalies],
        )

        # ── PLAN + EXECUTE ────────────────────────────────────────────────────
        # Group anomalies by priority and convert to evolution requests
        requests = self._anomalies_to_requests(snapshot)

        for request_text, anomaly in requests:
            async with self._evolution_semaphore:
                logger.info(
                    "mape_k.execute.start",
                    anomaly_type=anomaly.type.value,
                    request_preview=request_text[:100],
                )
                ctx = await self.run(
                    user_request=request_text,
                    dry_run=False,
                    source=EvolutionSource.MONITOR,
                    runtime_snapshot=snapshot,
                )
                # ── KNOWLEDGE ─────────────────────────────────────────────────
                logger.info(
                    "mape_k.knowledge.record",
                    anomaly_type=anomaly.type.value,
                    evolution_status=ctx.status.value,
                    request_id=ctx.request_id,
                )

    def _anomalies_to_requests(
        self, snapshot: RuntimeSnapshot
    ) -> list[tuple[str, Anomaly]]:
        """Convert detected anomalies into prioritized evolution requests.

        Returns a list of (request_text, anomaly) tuples sorted by severity.
        """
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        sorted_anomalies = sorted(
            snapshot.anomalies,
            key=lambda a: severity_order.get(a.severity, 9),
        )

        requests: list[tuple[str, Anomaly]] = []
        for anomaly in sorted_anomalies:
            text = self._build_request_from_anomaly(anomaly, snapshot)
            if text:
                requests.append((text, anomaly))

        return requests

    def _build_request_from_anomaly(
        self, anomaly: Anomaly, snapshot: RuntimeSnapshot
    ) -> str:
        """Build a natural language evolution request from a runtime anomaly.

        The request is passed to the Leader agent as if it were a user message.
        It includes all the evidence the engine needs to generate a correct fix.
        """
        error_context = ""
        if snapshot.recent_errors:
            recent = snapshot.recent_errors[:3]
            formatted = "\n".join(
                f"  - [{e.get('status_code', '?')}] {e.get('method', '?')} "
                f"{e.get('path', '?')}: {e.get('error_type', e.get('detail', ''))}"
                for e in recent
            )
            error_context = f"\n\nRecent errors observed at runtime:\n{formatted}"

        templates: dict[AnomalyType, str] = {
            AnomalyType.HIGH_ERROR_RATE: (
                f"The Managed System has a {snapshot.global_error_rate:.1%} error rate "
                f"(threshold: {self.config.monitor_error_rate_threshold:.0%}). "
                f"There have been {snapshot.total_errors} errors out of "
                f"{snapshot.total_requests} requests."
                f"{error_context}\n\n"
                f"Investigate the root cause in the backend code, fix the bug, "
                f"and add a regression test. Evidence: {anomaly.evidence}"
            ),
            AnomalyType.HIGH_LATENCY: (
                f"The endpoint {anomaly.evidence.get('method')} "
                f"{anomaly.evidence.get('path')} has degraded to "
                f"{anomaly.evidence.get('avg_latency_ms', '?'):.0f}ms average latency "
                f"(threshold: {self.config.monitor_latency_threshold_ms:.0f}ms). "
                f"Profile the endpoint, identify the bottleneck (missing index, N+1 query, "
                f"or missing cache), and implement the fix."
            ),
            AnomalyType.DATABASE_DEGRADED: (
                f"The database is reporting degraded health: {anomaly.description}. "
                f"Evidence: {anomaly.evidence}. "
                f"Fix the root cause — check for missing indexes, long-running queries, "
                f"or schema migration issues."
            ),
            AnomalyType.SERVICE_UNREACHABLE: (
                f"A Managed System service is unreachable or crashed: {anomaly.description}. "
                f"Evidence: {anomaly.evidence}. "
                f"Investigate the startup failure — check dependencies, environment variables, "
                f"and recent changes that may have introduced the regression."
            ),
            AnomalyType.REPEATED_EXCEPTION: (
                f"A recurring exception has been detected at runtime: {anomaly.description}. "
                f"Evidence: {anomaly.evidence}. "
                f"Find the root cause in the backend code and implement a fix with a test."
            ),
            AnomalyType.SCHEMA_DRIFT: (
                f"The database schema changed unexpectedly: {anomaly.description}. "
                f"Evidence: {anomaly.evidence}. "
                f"Verify whether this change is intentional. If not, create a corrective migration."
            ),
        }

        request_text = templates.get(anomaly.type, "")
        if not request_text:
            logger.warning("anomaly.no_template", anomaly_type=anomaly.type.value)

        return request_text

    # -----------------------------------------------------------------------
    # Internal — state machine
    # -----------------------------------------------------------------------

    def _get_agent_for_status(self, status: EvolutionStatus) -> BaseAgent | None:
        """Map a pipeline status to the agent that should run next."""
        mapping: dict[EvolutionStatus, BaseAgent] = {
            EvolutionStatus.RECEIVED: self.leader,
            EvolutionStatus.ANALYZING: self.data_manager,
            EvolutionStatus.GENERATING: self.generator,
            EvolutionStatus.VALIDATING: self.validator,
        }
        return mapping.get(status)

    async def _deploy(self, ctx: EvolutionContext) -> EvolutionContext:
        """Handle the deployment phase: local git commit + Docker rebuild.

        Deploys to the local evolved-app directory. Never pushes to GitHub.
        The open-source repo stays clean — only framework code lives there.
        """
        if ctx.request.dry_run:
            logger.info("deploy.dry_run", request_id=ctx.request_id)
            return ctx.model_copy(
                update={
                    "status": EvolutionStatus.COMPLETED,
                    "deployment_result": DeploymentResult(
                        success=True,
                        message="Dry run — skipped deployment",
                    ),
                }
            )

        result = await self.deployer.deploy(ctx)
        ctx = ctx.add_event(
            "deployer", "local_deploy", "completed" if result.success else "failed"
        )

        if not result.success:
            return ctx.fail(f"Deployment failed: {result.message}")

        return ctx.model_copy(
            update={
                "deployment_result": result,
                "status": EvolutionStatus.COMPLETED,
            }
        )

    async def _run_state_machine(self, ctx: EvolutionContext) -> EvolutionContext:
        """Execute the evolution state machine until COMPLETED or FAILED."""
        max_steps = 20
        step = 0

        while step < max_steps:
            step += 1

            if ctx.status in (EvolutionStatus.COMPLETED, EvolutionStatus.FAILED):
                break

            if ctx.status == EvolutionStatus.DEPLOYING:
                ctx = await self._deploy(ctx)
                continue

            agent = self._get_agent_for_status(ctx.status)
            if agent is None:
                ctx = ctx.fail(f"No agent registered for status: {ctx.status}")
                break

            logger.info(
                "pipeline.step",
                step=step,
                status=ctx.status.value,
                agent=agent.name,
                request_id=ctx.request_id,
            )
            ctx = await agent.execute(ctx)

        if step >= max_steps:
            ctx = ctx.fail("Pipeline exceeded maximum step limit")

        return ctx
