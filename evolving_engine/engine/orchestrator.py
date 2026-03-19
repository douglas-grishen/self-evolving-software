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
import hashlib
import time
from pathlib import Path

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

# Proactive analysis runs at most once every 60 minutes (unless manually triggered)
_PROACTIVE_INTERVAL_SECONDS = 60 * 60  # 60 minutes


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

        # Event reporter — fire-and-forget communication with backend API
        # Must be created before DataManagerAgent (which receives a reference to it)
        self.event_reporter = EventReporter(self.config.monitor_url)

        # Agents (shared by both triggered and continuous modes)
        self.leader = LeaderAgent(
            provider=self.provider,
            purpose=self.purpose,
            config=self.config,
        )
        self.data_manager = DataManagerAgent(
            config=self.config,
            event_reporter=self.event_reporter,  # enables lesson fetching per cycle
        )
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
        self._last_proactive_run: float = 0.0  # epoch timestamp of last proactive analysis

        # Proactive analysis cache — skip LLM call if Purpose + codebase unchanged
        self._last_analysis_hash: str = ""
        self._last_analysis_result: str = ""

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

        # Auto-extract a lesson if this cycle failed or had validation retries
        should_extract = ctx.status.value == "failed" or (
            ctx.status.value == "completed" and ctx.retry_count > 0
        )
        if should_extract:
            await self._extract_lesson_from_failure(ctx)

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

                # Hot-reload proactive interval from settings API
                try:
                    interval_str = await self.event_reporter.get_setting("proactive_interval_minutes")
                    if interval_str:
                        interval_minutes = int(interval_str)
                        proactive_interval = max(300, interval_minutes * 60)  # min 5 min
                    else:
                        proactive_interval = _PROACTIVE_INTERVAL_SECONDS
                except Exception:
                    proactive_interval = _PROACTIVE_INTERVAL_SECONDS

                # Proactive evolution (Purpose → build features)
                # Runs every 60 minutes OR when manually triggered via UI
                # OR immediately on first cycle when no apps exist yet
                if self.purpose:
                    should_run_proactive = False
                    elapsed = time.time() - self._last_proactive_run

                    # Check if manually triggered via API
                    triggered = await self.event_reporter.check_analysis_trigger()
                    if triggered:
                        logger.info("proactive.manual_trigger_detected")
                        should_run_proactive = True
                    elif elapsed >= proactive_interval:
                        logger.info(
                            "proactive.interval_reached",
                            minutes_elapsed=int(elapsed / 60),
                        )
                        should_run_proactive = True
                    elif elapsed >= 60:
                        # Bootstrap shortcut: if no apps exist yet and it's been at
                        # least 1 minute since last run, trigger immediately so the
                        # desktop populates without waiting a full hour.
                        apps = await self.event_reporter.fetch_apps()
                        if not apps:
                            logger.info("proactive.no_apps_bootstrap_trigger")
                            should_run_proactive = True

                    if should_run_proactive:
                        success = await self._proactive_evolution()
                        if not success:
                            # Reset timer so we retry sooner (5 min) instead of waiting 60
                            self._last_proactive_run = time.time() - _PROACTIVE_INTERVAL_SECONDS + 300

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

    async def _proactive_evolution(self) -> bool:
        """Analyze the Purpose against the current codebase and evolve proactively.

        Optimizations applied:
        1. Uses Haiku (fast model) for gap analysis — 15x cheaper than Sonnet
        2. Limits scope to 3-5 files per cycle for higher quality
        3. Caches analysis — skips LLM call if Purpose + codebase haven't changed

        Returns True if the analysis completed (even if no evolution was needed),
        False if there was an error.
        """
        async with self._evolution_semaphore:
            self._last_proactive_run = time.time()
            logger.info("proactive.analyzing_purpose", purpose_version=self.purpose.version)

            # Build a codebase summary by scanning the evolved (deployed) code
            try:
                scan_path = self.config.evolved_app_path
                if not scan_path.exists():
                    scan_path = self.config.managed_app_path
                codebase_summary = await self._build_codebase_summary(scan_path)
            except Exception as exc:
                logger.warning("proactive.scan_error", error=str(exc))
                return False

            # Fetch existing apps to understand what has already been planned/built
            apps_summary = await self._fetch_apps_summary()

            # ── OPTIMIZATION #3: Cache — skip LLM if nothing changed ──────────
            purpose_yaml = self.purpose.to_yaml_string()
            cache_input = f"{purpose_yaml}\n---\n{codebase_summary}\n---\n{apps_summary}"
            current_hash = hashlib.sha256(cache_input.encode()).hexdigest()[:16]

            if current_hash == self._last_analysis_hash and self._last_analysis_result:
                # Nothing changed since last analysis. If last result was
                # NO_EVOLUTION_NEEDED, skip entirely. Otherwise re-execute
                # the cached evolution request (it may have failed last time).
                if "NO_EVOLUTION_NEEDED" in self._last_analysis_result:
                    logger.info(
                        "proactive.cache_hit_skip",
                        hash=current_hash,
                        msg="Purpose and codebase unchanged, no evolution needed",
                    )
                    return True
                else:
                    logger.info(
                        "proactive.cache_hit_retry",
                        hash=current_hash,
                        msg="Re-executing cached analysis (may have failed previously)",
                    )
                    analysis = self._last_analysis_result
                    return await self._execute_proactive_analysis(analysis)

            # ── OPTIMIZATION #1: Use Haiku for gap analysis ───────────────────
            purpose_context = self.purpose.to_prompt_context()

            analysis_prompt = f"""You are the proactive evolution analyzer for a self-evolving software system.

Your job is to compare the system's Purpose (its specification of what it should be) against
the current codebase, and identify the SINGLE most important gap — a feature, improvement,
or requirement that is defined in the Purpose but NOT yet implemented in the code.

{purpose_context}

## Current Codebase Summary
{codebase_summary}

## Existing Apps
{apps_summary}

## Architecture: Apps, Features & Capabilities

The system uses a structured framework:
- **App**: A cohesive unit with a concrete goal, displayed as a desktop icon. Users launch apps by clicking icons.
- **Feature**: A user-facing behavior within an app (something a person can see/use).
- **Capability**: An internal system ability that enables features or runs independently in the background.

When you identify a gap that warrants a NEW app (a distinct user-facing experience), you should
create the app with its features and capabilities using the CREATE_APP format below.

When the gap is an improvement to existing code or infrastructure (not a new app), use EVOLVE.

## IMPORTANT CONSTRAINTS
- Focus on ONE small, incremental change (max 3-5 files)
- Do NOT try to build an entire app in one shot — start with the smallest viable piece
- Prefer backend-first changes (API endpoints, models) over full-stack features
- Each evolution_request should be achievable in a single code generation pass

## CRITICAL RULE: When No Apps Exist
If the Existing Apps section shows "(No apps created yet)", you MUST use CREATE_APP.
Do NOT use EVOLVE to add backend services or infrastructure when there are no apps yet —
that produces orphaned code that has no user-facing purpose. Always start with CREATE_APP
to register the concept first, then use EVOLVE in subsequent cycles to build its internals.

## Instructions
1. Carefully compare each Purpose requirement against the codebase and existing apps
2. Identify requirements that are NOT yet implemented or are only partially implemented
3. Pick the SINGLE most impactful gap to close next
4. If ALL requirements appear to be implemented, respond with exactly: NO_EVOLUTION_NEEDED

## Response Format

If a new App should be created:
CREATE_APP:
name: <App name>
icon: <single emoji>
goal: <concrete objective>
features:
  - name: <feature name>
    description: <what the user experiences>
capabilities:
  - name: <capability name>
    description: <internal system ability>
    is_background: <true/false>
evolution_request: <Describe ONLY the first 3-5 files needed to start this app. Be specific about file paths and what each file should contain. Do NOT describe the entire app — just the foundation.>

If existing code needs improvement (not a new app):
EVOLVE: <A clear, actionable description of what to build/implement to close the gap.
Include which Purpose requirement it fulfills and what specific code changes are needed.
Limit to max 3-5 files.>

If no evolution is needed:
NO_EVOLUTION_NEEDED
"""

            try:
                # Use fast model (Haiku) for analysis — 15x cheaper
                response = await self.provider.generate(
                    system_prompt="You are a senior software architect analyzing a self-evolving system.",
                    user_prompt=analysis_prompt,
                    max_tokens=2048,
                    model_override="fast",
                )
                analysis = response.strip()
            except Exception as exc:
                logger.warning("proactive.llm_error", error=str(exc))
                return False

            # Cache the result
            self._last_analysis_hash = current_hash
            self._last_analysis_result = analysis

            return await self._execute_proactive_analysis(analysis)

    async def _execute_proactive_analysis(self, analysis: str) -> bool:
        """Execute the result of a proactive analysis (either fresh or cached)."""
        if "NO_EVOLUTION_NEEDED" in analysis:
            logger.info("proactive.all_requirements_met")
            return True

        # Handle CREATE_APP response — register the app first, then evolve
        if analysis.startswith("CREATE_APP:"):
            await self._handle_create_app(analysis)
            return True

        # Extract the evolution request
        if analysis.startswith("EVOLVE:"):
            request_text = analysis[len("EVOLVE:"):].strip()
        else:
            request_text = analysis

        logger.info(
            "proactive.evolution_needed",
            request_preview=request_text[:150],
        )

        # Bootstrap guard: if no apps exist yet, auto-create a stub app from
        # the Purpose before running EVOLVE. This handles the case where the
        # LLM ignores the CRITICAL RULE and returns EVOLVE instead of CREATE_APP.
        app_id: str | None = None
        app_name: str = ""
        existing_apps = await self.event_reporter.fetch_apps()
        if not existing_apps and self.purpose:
            try:
                app_name = self.purpose.identity.name.title()
                app_goal = self.purpose.identity.description
                app_payload = {
                    "name": app_name,
                    "icon": "🔍",
                    "goal": app_goal,
                    "status": "building",
                    "features": [],
                    "capability_ids": [],
                }
                app_id = await self.event_reporter.create_app(app_payload)
                if app_id:
                    logger.info("proactive.auto_bootstrapped_app", app_id=app_id, name=app_name)
                    # Clear cache so next cycle sees the new app
                    self._last_analysis_hash = ""
                    self._last_analysis_result = ""
            except Exception as exc:
                logger.warning("proactive.auto_bootstrap_error", error=str(exc))

        # Execute the evolution pipeline (Generator still uses Sonnet)
        ctx = await self.run(
            user_request=f"[Proactive — Purpose-driven] {request_text}",
            dry_run=False,
            source=EvolutionSource.MONITOR,
        )

        # Clear cache on successful evolution (codebase changed)
        if ctx.status == EvolutionStatus.COMPLETED:
            self._last_analysis_hash = ""
            self._last_analysis_result = ""
            # Mark auto-bootstrapped app as active
            if app_id:
                await self.event_reporter.update_app(app_id, {"status": "active"})
                logger.info("proactive.app_activated", app_id=app_id, name=app_name)

        logger.info(
            "proactive.evolution_complete",
            status=ctx.status.value,
            request_id=ctx.request_id,
        )
        return ctx.status == EvolutionStatus.COMPLETED

    async def _handle_create_app(self, analysis: str) -> None:
        """Parse CREATE_APP response from LLM and register it via the backend API.

        Then runs the evolution pipeline to build the app's first feature.
        """
        import yaml  # local import to avoid top-level dependency in this module

        try:
            # Extract YAML-like block after CREATE_APP:
            yaml_text = analysis[len("CREATE_APP:"):].strip()

            # Extract the evolution_request from the end (not valid YAML key)
            evolution_request = ""
            if "evolution_request:" in yaml_text:
                parts = yaml_text.split("evolution_request:", 1)
                yaml_text = parts[0]
                evolution_request = parts[1].strip()

            # Parse the YAML-like structure
            app_data = yaml.safe_load(yaml_text) or {}

            app_name = app_data.get("name", "Unnamed App")
            app_icon = app_data.get("icon", "\U0001f4e6")
            app_goal = app_data.get("goal", "")
            features_raw = app_data.get("features", [])
            capabilities_raw = app_data.get("capabilities", [])

            logger.info(
                "proactive.create_app",
                name=app_name,
                features=len(features_raw),
                capabilities=len(capabilities_raw),
            )

            # Create capabilities first
            cap_ids: list[str] = []
            for cap_data in capabilities_raw:
                cap_payload = {
                    "name": cap_data.get("name", ""),
                    "description": cap_data.get("description", ""),
                    "is_background": cap_data.get("is_background", False),
                }
                cap_id = await self.event_reporter.create_capability(cap_payload)
                if cap_id:
                    cap_ids.append(cap_id)

            # Build features list with capability links
            features_payload = []
            for feat_data in features_raw:
                features_payload.append({
                    "name": feat_data.get("name", ""),
                    "description": feat_data.get("description", ""),
                    "user_facing_description": feat_data.get("description", ""),
                    "capability_ids": cap_ids,  # link all caps to all features for now
                })

            # Create the app
            app_payload = {
                "name": app_name,
                "icon": app_icon,
                "goal": app_goal,
                "status": "building",
                "features": features_payload,
                "capability_ids": cap_ids,
            }
            app_id = await self.event_reporter.create_app(app_payload)

            if app_id:
                logger.info("proactive.app_created", app_id=app_id, name=app_name)
            else:
                logger.warning("proactive.app_create_failed", name=app_name)

            # Now run the evolution to actually build the app
            if evolution_request:
                ctx = await self.run(
                    user_request=f"[Proactive — App: {app_name}] {evolution_request}",
                    dry_run=False,
                    source=EvolutionSource.MONITOR,
                )
                logger.info(
                    "proactive.app_evolution_complete",
                    app_name=app_name,
                    status=ctx.status.value,
                    request_id=ctx.request_id,
                )

                # Mark app as active once the first evolution succeeds
                if app_id and ctx.status == EvolutionStatus.COMPLETED:
                    await self.event_reporter.update_app(app_id, {"status": "active"})
                    logger.info("proactive.app_activated", app_id=app_id, name=app_name)

        except Exception as exc:
            logger.warning("proactive.create_app_error", error=str(exc))

    async def _fetch_apps_summary(self) -> str:
        """Fetch the list of existing apps from the backend for the proactive analyzer."""
        try:
            apps = await self.event_reporter.fetch_apps()
            if not apps:
                return "(No apps created yet)"

            lines = []
            for app in apps:
                feat_count = app.get("feature_count", 0)
                cap_count = app.get("capability_count", 0)
                lines.append(
                    f"- {app.get('icon', '')} **{app.get('name', '?')}** "
                    f"[{app.get('status', '?')}] — {app.get('goal', 'no goal')} "
                    f"({feat_count} features, {cap_count} capabilities)"
                )
            return "\n".join(lines)
        except Exception as exc:
            logger.debug("proactive.fetch_apps_error", error=str(exc))
            return "(Could not fetch apps)"

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
    # Internal — lesson extraction
    # -----------------------------------------------------------------------

    async def _extract_lesson_from_failure(self, ctx: EvolutionContext) -> None:
        """Analyze a failed or retried evolution and auto-generate a lesson.

        Called after post_event() when:
          - ctx.status == FAILED  (all retries exhausted)
          - ctx.status == COMPLETED and ctx.retry_count > 0  (had validation failures)

        Uses the fast model. Fire-and-forget — never raises.
        """
        error_text = ctx.error or ""
        validation_errors: list[str] = []
        if ctx.validation_result and ctx.validation_result.errors:
            validation_errors = ctx.validation_result.errors

        if not error_text and not validation_errors:
            return

        combined_errors = error_text
        if validation_errors:
            combined_errors += "\n\nValidation errors:\n" + "\n".join(
                f"- {e}" for e in validation_errors[:10]
            )

        context_label = (
            "complete failure (all retries exhausted)"
            if ctx.status.value == "failed"
            else f"partial failure (succeeded after {ctx.retry_count} retries)"
        )

        extraction_prompt = (
            "You are a software engineering lessons-learned extractor.\n\n"
            f"An autonomous code-generation engine had a {context_label} "
            "while evolving a FastAPI + React application.\n\n"
            "## Error / Failure Evidence\n"
            f"{combined_errors[:3000]}\n\n"
            "## Evolution Request\n"
            f"{ctx.request.user_request[:400]}\n\n"
            "## Task\n"
            "Determine if this failure reveals a REUSABLE lesson — a coding pattern "
            "the engine should remember and avoid in future cycles.\n\n"
            "If yes, respond with EXACTLY this JSON (no markdown, no explanation):\n"
            '{"category": "<forbidden_pattern|best_practice|bug_fix|architecture_note>", '
            '"title": "<short title max 120 chars>", '
            '"content": "<1-3 sentences, specific and actionable>", '
            '"severity": "<critical|warning>"}\n\n'
            "severity=critical ONLY if repeating this will always cause a crash.\n"
            "Be specific: include exact symbol/name/path to avoid.\n"
            "If the failure is transient (network, timeout, environment), "
            "respond with exactly: NO_LESSON"
        )

        try:
            response = await self.provider.generate(
                system_prompt=(
                    "You are a senior software engineer extracting actionable lessons "
                    "from code generation failures."
                ),
                user_prompt=extraction_prompt,
                max_tokens=512,
                model_override="fast",
            )
            text = response.strip()

            if "NO_LESSON" in text:
                logger.info(
                    "lesson_extraction.no_lesson",
                    request_id=ctx.request_id,
                )
                return

            import json as _json
            lesson_data = _json.loads(text)

            await self.event_reporter.post_lesson(
                category=lesson_data["category"],
                title=lesson_data["title"],
                content=lesson_data["content"],
                severity=lesson_data["severity"],
                source="auto",
            )
            logger.info(
                "lesson_extraction.posted",
                request_id=ctx.request_id,
                title=lesson_data.get("title", "")[:80],
                severity=lesson_data.get("severity"),
            )

        except Exception as exc:
            logger.debug(
                "lesson_extraction.error",
                request_id=ctx.request_id,
                error=str(exc),
            )

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
