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
from engine.agents.purpose_evolver import PurposeEvolver
from engine.agents.validator import CodeValidatorAgent
from engine.config import EngineSettings, settings
from engine.context import EvolutionContext, create_context
from engine.deployer.git_ops import LocalDeployer
from engine.event_reporter import EventReporter
from engine.models.evolution import DeploymentResult, EvolutionStatus, EvolutionSource
from engine.models.genesis import Genesis
from engine.models.inception import InceptionRequest
from engine.models.purpose import Purpose
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

        # Genesis — the immutable initial state of the system
        self.genesis = self._load_genesis()

        # Purpose — the guiding specification for all evolution decisions
        self.purpose = self._load_purpose()

        # Agents (shared by both triggered and continuous modes)
        self.leader = LeaderAgent(
            provider=self.provider,
            purpose=self.purpose,
            config=self.config,
        )
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

        # Event reporter — fire-and-forget communication with backend API
        self.event_reporter = EventReporter(self.config.monitor_url)

        # Purpose evolver — processes Inceptions to modify the Purpose
        self.purpose_evolver = PurposeEvolver(
            provider=self.provider,
            config=self.config,
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

        # Report pipeline start to backend
        await self.event_reporter.post_event(ctx)

        ctx = await self._run_state_machine(ctx)

        # Report pipeline completion to backend
        await self.event_reporter.post_event(ctx)

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
          1. FETCH PURPOSE — load latest Purpose from backend API (admin-defined)
          2. PROCESS INCEPTIONS — apply pending Purpose modifications
          3. MONITOR  — observe the Managed System via the control-plane
          4. ANALYZE  — detect anomalies in the snapshot
          5. PLAN     — convert anomalies into evolution requests (reactive)
          6. PROACTIVE ANALYZE — compare Purpose vs codebase, find gaps
          7. EXECUTE  — run the evolution pipeline for each request
          8. KNOWLEDGE — log outcomes; the next iteration learns from them
          9. Wait for the configured interval, then repeat

        This method runs until cancelled (e.g., via KeyboardInterrupt or
        Docker SIGTERM). It never raises — all errors are logged and the
        loop continues on the next interval.
        """
        self._running = True
        interval = self.config.monitor_interval_seconds

        # Fetch Purpose from backend API (admin defined it via UI)
        if not self.purpose:
            self.purpose = await self._fetch_purpose_from_api()
            if self.purpose:
                self.leader.purpose = self.purpose

        logger.info(
            "continuous_loop.start",
            monitor_url=self.config.monitor_url,
            interval_seconds=interval,
            genesis_version=self.genesis.version if self.genesis else None,
            purpose_version=self.purpose.version if self.purpose else None,
        )

        # Post initial purpose to backend so the UI can display it
        if self.purpose:
            await self.event_reporter.post_purpose(self.purpose)

        while self._running:
            try:
                # Process pending Inceptions before monitoring
                await self._process_pending_inceptions()

                # Refresh Purpose from API if we don't have one yet
                if not self.purpose:
                    self.purpose = await self._fetch_purpose_from_api()
                    if self.purpose:
                        self.leader.purpose = self.purpose
                        logger.info(
                            "purpose.loaded_from_api",
                            version=self.purpose.version,
                            identity=self.purpose.identity.name,
                        )

                # Reactive monitoring (anomalies → fix bugs)
                await self._mape_k_iteration()

                # Proactive evolution (Purpose → build features)
                if self.purpose:
                    await self._proactive_evolution()

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
    # Internal — Proactive evolution (Purpose-driven)
    # -----------------------------------------------------------------------

    async def _proactive_evolution(self) -> None:
        """Analyze the Purpose against the current codebase and evolve proactively.

        Unlike reactive evolution (which responds to runtime anomalies), proactive
        evolution reads the Purpose requirements, scans the codebase, and identifies
        features or improvements that haven't been implemented yet.

        This runs once per MAPE-K iteration. The LLM decides whether there is
        meaningful work to do, and generates a prioritized evolution request.
        """
        async with self._evolution_semaphore:
            logger.info("proactive.analyzing_purpose", purpose_version=self.purpose.version)

            # Build a codebase summary by scanning the evolved (deployed) code
            try:
                scan_path = self.config.evolved_app_path
                if not scan_path.exists():
                    scan_path = self.config.managed_app_path
                codebase_summary = await self._build_codebase_summary(scan_path)
            except Exception as exc:
                logger.warning("proactive.scan_error", error=str(exc))
                return

            # Ask the LLM to compare Purpose vs codebase
            purpose_context = self.purpose.to_prompt_context()

            analysis_prompt = f"""You are the proactive evolution analyzer for a self-evolving software system.

Your job is to compare the system's Purpose (its specification of what it should be) against
the current codebase, and identify the SINGLE most important gap — a feature, improvement,
or requirement that is defined in the Purpose but NOT yet implemented in the code.

{purpose_context}

## Current Codebase Summary
{codebase_summary}

## Instructions
1. Carefully compare each Purpose requirement against the codebase
2. Identify requirements that are NOT yet implemented or are only partially implemented
3. Pick the SINGLE most impactful gap to close next
4. If ALL requirements appear to be implemented, respond with exactly: NO_EVOLUTION_NEEDED

Respond in one of these formats:

If evolution is needed:
EVOLVE: <A clear, actionable description of what to build/implement to close the gap.
Include which Purpose requirement it fulfills and what specific code changes are needed.>

If no evolution is needed:
NO_EVOLUTION_NEEDED
"""

            try:
                response = await self.provider.generate(
                    system_prompt="You are a senior software architect analyzing a self-evolving system.",
                    user_prompt=analysis_prompt,
                    max_tokens=2048,
                )
                analysis = response.strip()
            except Exception as exc:
                logger.warning("proactive.llm_error", error=str(exc))
                return

            if "NO_EVOLUTION_NEEDED" in analysis:
                logger.info("proactive.all_requirements_met")
                return

            # Extract the evolution request
            if analysis.startswith("EVOLVE:"):
                request_text = analysis[len("EVOLVE:"):].strip()
            else:
                request_text = analysis

            logger.info(
                "proactive.evolution_needed",
                request_preview=request_text[:150],
            )

            # Execute the evolution pipeline
            ctx = await self.run(
                user_request=f"[Proactive — Purpose-driven] {request_text}",
                dry_run=False,
                source=EvolutionSource.MONITOR,
            )

            logger.info(
                "proactive.evolution_complete",
                status=ctx.status.value,
                request_id=ctx.request_id,
            )

    async def _build_codebase_summary(self, app_path: Path) -> str:
        """Build a quick summary of the codebase for the proactive analyzer.

        Scans key files (routes, models, components) to understand what's implemented.
        """
        summary_parts: list[str] = []

        # Scan backend
        backend_path = app_path / "backend" if (app_path / "backend").exists() else app_path
        for subdir in ["app/api", "app/models", "app/schemas"]:
            dir_path = backend_path / subdir
            if dir_path.exists():
                files = sorted(dir_path.rglob("*.py"))
                for f in files[:20]:  # limit to avoid huge prompts
                    try:
                        content = f.read_text()[:2000]
                        rel = f.relative_to(app_path)
                        summary_parts.append(f"### {rel}\n```python\n{content}\n```")
                    except Exception:
                        pass

        # Scan frontend
        frontend_path = app_path / "frontend" if (app_path / "frontend").exists() else app_path
        for subdir in ["src/components", "src/hooks", "src"]:
            dir_path = frontend_path / subdir
            if dir_path.exists():
                files = sorted(dir_path.glob("*.tsx")) + sorted(dir_path.glob("*.ts"))
                for f in files[:15]:
                    try:
                        content = f.read_text()[:2000]
                        rel = f.relative_to(app_path)
                        summary_parts.append(f"### {rel}\n```typescript\n{content}\n```")
                    except Exception:
                        pass

        if not summary_parts:
            return "(Could not scan codebase — no files found)"

        return "\n\n".join(summary_parts[:30])  # cap at 30 files

    # -----------------------------------------------------------------------
    # Internal — Inception processing
    # -----------------------------------------------------------------------

    async def _process_pending_inceptions(self) -> None:
        """Poll the backend for pending Inceptions and process them.

        Inceptions modify the Purpose. Each pending inception is fed to the
        PurposeEvolver, which uses the LLM to produce an updated Purpose.
        This runs before each MAPE-K iteration so the monitoring loop
        always uses the latest Purpose.
        """
        if not self.purpose:
            return

        inceptions = await self.event_reporter.poll_inceptions()
        if not inceptions:
            return

        for inception in inceptions:
            logger.info(
                "inception.processing",
                inception_id=inception.id,
                directive=inception.directive[:100],
            )

            new_purpose, result = await self.purpose_evolver.evolve(
                self.purpose, inception
            )

            accepted = new_purpose.version > self.purpose.version

            # Report result back to backend
            await self.event_reporter.report_inception_result(
                inception.id, result, accepted=accepted
            )

            if accepted:
                # Update in-memory purpose and propagate to Leader agent
                self.purpose = new_purpose
                self.leader.purpose = new_purpose

                # Store new purpose version in backend DB
                await self.event_reporter.post_purpose(new_purpose, inception_id=inception.id)

                logger.info(
                    "inception.applied",
                    inception_id=inception.id,
                    new_version=new_purpose.version,
                )
            else:
                logger.info(
                    "inception.rejected",
                    inception_id=inception.id,
                    reason=result.changes_summary[:200],
                )

    # -----------------------------------------------------------------------
    # Internal — Genesis & Purpose loading
    # -----------------------------------------------------------------------

    def _load_genesis(self) -> Genesis | None:
        """Load the Genesis snapshot from disk. Returns None if not found."""
        try:
            genesis = Genesis.load(self.config.genesis_path)
            logger.info(
                "genesis.loaded",
                version=genesis.version,
                created_at=genesis.created_at.isoformat(),
            )
            return genesis
        except FileNotFoundError:
            logger.warning("genesis.not_found", path=str(self.config.genesis_path))
            return None
        except Exception as exc:
            logger.warning("genesis.load_error", error=str(exc))
            return None

    def _load_purpose(self) -> Purpose | None:
        """Load the current Purpose from disk. Returns None if not found."""
        try:
            purpose = Purpose.load(self.config.purpose_path)
            logger.info(
                "purpose.loaded",
                version=purpose.version,
                identity=purpose.identity.name,
            )
            return purpose
        except FileNotFoundError:
            logger.warning("purpose.not_found", path=str(self.config.purpose_path))
            return None
        except Exception as exc:
            logger.warning("purpose.load_error", error=str(exc))
            return None

    async def _fetch_purpose_from_api(self) -> Purpose | None:
        """Try to load Purpose from backend API (defined by admin via UI).

        Falls back to local file if the API is not available.
        """
        purpose = await self.event_reporter.fetch_purpose()
        if purpose:
            return purpose
        # Fallback to local file
        return self._load_purpose()

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
