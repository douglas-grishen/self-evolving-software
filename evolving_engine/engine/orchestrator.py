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
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from engine.models.backlog import (
    BacklogAppSpec,
    BacklogItem,
    BacklogPlannerResponse,
    BacklogTaskPriority,
    BacklogTaskStatus,
    BacklogTaskType,
)
from engine.models.evolution import DeploymentResult, EvolutionStatus, EvolutionSource
from engine.models.genesis import Genesis
from engine.models.inception import InceptionRequest
from engine.models.purpose import Purpose
from engine.monitor.models import Anomaly, AnomalyType, RuntimeSnapshot
from engine.monitor.observer import RuntimeObserver
from engine.providers.anthropic_provider import AnthropicProvider
from engine.providers.base import BaseLLMProvider
from engine.providers.bedrock_provider import BedrockProvider
from engine.providers.openai_provider import OpenAIProvider
from engine.repo.scanner import build_repo_map, canonicalize_frontend_app_key
from engine.sandbox.base import BaseSandbox
from engine.sandbox.docker_sandbox import DockerSandbox
from engine.usage_tracker import UsageTracker

logger = structlog.get_logger()

# How many anomaly-driven evolutions can run concurrently (prevent storm)
_MAX_CONCURRENT_EVOLUTIONS = 1

# Proactive analysis runs at most once every 60 minutes (unless manually triggered)
_PROACTIVE_INTERVAL_SECONDS = 60 * 60  # 60 minutes
_CONTROL_PLANE_RETRY_SECONDS = 10
_BACKLOG_STALE_IN_PROGRESS_SECONDS = 90 * 60
_BACKLOG_BLOCK_AFTER_FAILURES = 3
_BACKLOG_BLOCK_AFTER_TOTAL_ATTEMPTS = 6
_BACKLOG_TRANSIENT_RETRY_SECONDS = 5 * 60
_BACKLOG_STRUCTURAL_RETRY_SCHEDULE_SECONDS = {
    1: 5 * 60,
    2: 30 * 60,
}
_BACKLOG_PRIORITY_ORDER = {
    BacklogTaskPriority.HIGH.value: 0,
    BacklogTaskPriority.NORMAL.value: 1,
    BacklogTaskPriority.LOW.value: 2,
}
_TRANSIENT_BACKLOG_ERROR_PATTERNS = (
    re.compile(r"timed?\s*out", re.IGNORECASE),
    re.compile(r"temporar(?:ily)? unavailable", re.IGNORECASE),
    re.compile(r"connection (?:refused|reset|error|closed)", re.IGNORECASE),
    re.compile(r"(?:502|503|504)\b"),
    re.compile(r"rate limit|throttl", re.IGNORECASE),
    re.compile(r"no space left on device|disk full|enospc", re.IGNORECASE),
    re.compile(r"backend .* unavailable", re.IGNORECASE),
    re.compile(r"control[_ -]?plane .* unavailable", re.IGNORECASE),
    re.compile(r"network", re.IGNORECASE),
)


def _frontend_entry_key(app_name: str) -> str:
    """Generate the stable frontend module key for a desktop app."""
    return canonicalize_frontend_app_key(app_name)


@dataclass
class BacklogProbeState:
    """Operational view of whether the persisted backlog can advance."""

    actionable_item: BacklogItem | None = None
    blocked_frontier_item: BacklogItem | None = None
    non_terminal_count: int = 0

    @property
    def is_stalled(self) -> bool:
        return self.actionable_item is None and self.blocked_frontier_item is not None


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
        self._provider_managed_externally = provider is not None
        if provider:
            self.provider = provider
        else:
            self.provider = self._build_provider()

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
        self._purpose_synced = False

        # Proactive planning cache — skip re-planning if the inputs are unchanged
        self._last_backlog_hash: str = ""
        self._last_llm_config_signature = self._current_llm_signature()
        self.usage_tracker = UsageTracker(self.config.usage_state_path)
        self.daily_llm_calls_limit = self.config.daily_llm_calls_limit
        self.daily_input_tokens_limit = self.config.daily_input_tokens_limit
        self.daily_output_tokens_limit = self.config.daily_output_tokens_limit
        self.daily_proactive_runs_limit = self.config.daily_proactive_runs_limit
        self.daily_failed_evolutions_limit = self.config.daily_failed_evolutions_limit
        self.daily_task_attempt_limit = self.config.daily_task_attempt_limit

    def _build_provider(self) -> BaseLLMProvider:
        """Instantiate the configured LLM provider."""
        if self.config.llm_provider == "bedrock":
            return BedrockProvider(self.config)
        if self.config.llm_provider == "openai":
            return OpenAIProvider(self.config)
        return AnthropicProvider(self.config)

    def _current_llm_signature(self) -> tuple[str, str, str, str]:
        """Return the settings that materially affect provider selection."""
        return (
            self.config.llm_provider,
            self.config.anthropic_api_key,
            self.config.openai_api_key,
            self._active_provider_model(),
        )

    def _active_provider_model(self) -> str:
        """Return the active default model for the currently selected provider."""
        if self.config.llm_provider == "bedrock":
            return self.config.bedrock_model_id
        if self.config.llm_provider == "openai":
            return self.config.openai_model
        return self.config.anthropic_model

    def _sync_agents_with_provider(self) -> None:
        """Push the current provider instance into the long-lived agents."""
        self.leader.provider = self.provider
        self.generator.provider = self.provider
        self.purpose_evolver.provider = self.provider

    async def _refresh_runtime_llm_config(self) -> None:
        """Hot-reload engine provider and model choices from persisted system settings."""
        if self._provider_managed_externally:
            return

        scoped_provider_value = (await self.event_reporter.get_setting("engine_llm_provider") or "").strip()
        legacy_provider_value = (await self.event_reporter.get_setting("llm_provider") or "").strip()
        selected_provider = (
            scoped_provider_value or legacy_provider_value or self.config.llm_provider or "anthropic"
        ).strip().lower()
        if selected_provider not in {"anthropic", "bedrock", "openai"}:
            logger.warning("llm_config.invalid_provider", provider=selected_provider)
            selected_provider = self.config.llm_provider

        scoped_model_value = (await self.event_reporter.get_setting("engine_llm_model") or "").strip()
        legacy_model_value = (await self.event_reporter.get_setting("llm_model") or "").strip()
        llm_model = scoped_model_value or legacy_model_value
        anthropic_api_key = await self.event_reporter.get_setting("anthropic_api_key")
        openai_api_key = await self.event_reporter.get_setting("openai_api_key")

        previous_state = (
            self.config.llm_provider,
            self.config.anthropic_api_key,
            self.config.anthropic_model,
            self.config.anthropic_model_fast,
            self.config.openai_api_key,
            self.config.openai_model,
            self.config.openai_model_fast,
            self.config.bedrock_model_id,
        )

        if anthropic_api_key is not None:
            self.config.anthropic_api_key = anthropic_api_key
        if openai_api_key is not None:
            self.config.openai_api_key = openai_api_key

        self.config.llm_provider = selected_provider
        if selected_provider == "bedrock":
            if llm_model:
                self.config.bedrock_model_id = llm_model
        elif selected_provider == "openai":
            if llm_model:
                self.config.openai_model = llm_model
                self.config.openai_model_fast = llm_model
        else:
            if llm_model:
                self.config.anthropic_model = llm_model
                self.config.anthropic_model_fast = llm_model

        current_signature = self._current_llm_signature()
        if current_signature == self._last_llm_config_signature:
            return

        attempted_provider = self.config.llm_provider
        attempted_model = self._active_provider_model()
        try:
            next_provider = self._build_provider()
        except Exception as exc:
            (
                self.config.llm_provider,
                self.config.anthropic_api_key,
                self.config.anthropic_model,
                self.config.anthropic_model_fast,
                self.config.openai_api_key,
                self.config.openai_model,
                self.config.openai_model_fast,
                self.config.bedrock_model_id,
            ) = previous_state
            logger.warning(
                "llm_config.reload_failed",
                provider=attempted_provider,
                model=attempted_model,
                error=str(exc),
            )
            return

        self.provider = next_provider
        self._sync_agents_with_provider()
        self._last_llm_config_signature = current_signature
        logger.info(
            "llm_config.reloaded",
            provider=self.config.llm_provider,
            model=self._active_provider_model(),
            anthropic_key_configured=bool(self.config.anthropic_api_key),
            openai_key_configured=bool(self.config.openai_api_key),
        )

    async def _refresh_runtime_guardrails(self) -> None:
        """Hot-reload daily autonomy/cost limits from persisted settings."""
        self.daily_llm_calls_limit = await self._read_int_setting(
            "engine_daily_llm_calls_limit",
            self.config.daily_llm_calls_limit,
            minimum=1,
        )
        self.daily_input_tokens_limit = await self._read_int_setting(
            "engine_daily_input_tokens_limit",
            self.config.daily_input_tokens_limit,
            minimum=1,
        )
        self.daily_output_tokens_limit = await self._read_int_setting(
            "engine_daily_output_tokens_limit",
            self.config.daily_output_tokens_limit,
            minimum=1,
        )
        self.daily_proactive_runs_limit = await self._read_int_setting(
            "engine_daily_proactive_runs_limit",
            self.config.daily_proactive_runs_limit,
            minimum=1,
        )
        self.daily_failed_evolutions_limit = await self._read_int_setting(
            "engine_daily_failed_evolutions_limit",
            self.config.daily_failed_evolutions_limit,
            minimum=1,
        )
        self.daily_task_attempt_limit = await self._read_int_setting(
            "engine_daily_task_attempt_limit",
            self.config.daily_task_attempt_limit,
            minimum=1,
        )

    async def _read_int_setting(self, key: str, default: int, *, minimum: int = 0) -> int:
        """Read a positive integer setting with a safe fallback."""
        raw = await self.event_reporter.get_setting(key)
        if raw is None:
            return default
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            logger.warning("settings.invalid_integer", key=key, value=raw, fallback=default)
            return default
        return max(minimum, value)

    async def _publish_usage_snapshot(self) -> None:
        """Expose the engine's UTC daily usage ledger through backend settings."""
        if not hasattr(self.event_reporter, "set_setting"):
            return
        try:
            snapshot = self.usage_tracker.snapshot()
            await self.event_reporter.set_setting(
                "engine_daily_usage_snapshot",
                json.dumps(snapshot, sort_keys=True),
            )
        except Exception as exc:
            logger.debug("usage_snapshot.publish_failed", error=str(exc))

    def _task_attempts_today(self, task_key: str) -> int:
        """Return today's starts for this task, tolerating absent tracker state in tests."""
        tracker = getattr(self, "usage_tracker", None)
        if tracker is None:
            return 0
        try:
            return tracker.task_attempts_today(task_key)
        except Exception:
            return 0

    def _daily_task_attempt_limit(self) -> int:
        """Return the configured per-task daily cap with a safe test-friendly fallback."""
        if hasattr(self, "daily_task_attempt_limit"):
            return int(getattr(self, "daily_task_attempt_limit"))
        config = getattr(self, "config", None)
        if config is not None and hasattr(config, "daily_task_attempt_limit"):
            return int(config.daily_task_attempt_limit)
        return 3

    def _proactive_budget_status(self) -> tuple[bool, str | None, dict]:
        """Decide whether proactive work is allowed under today's budgets."""
        tracker = getattr(self, "usage_tracker", None)
        if tracker is None:
            return True, None, {}

        snapshot = tracker.snapshot()
        checks = (
            ("llm_calls", self.daily_llm_calls_limit, "daily_llm_calls_limit"),
            ("input_tokens", self.daily_input_tokens_limit, "daily_input_tokens_limit"),
            ("output_tokens", self.daily_output_tokens_limit, "daily_output_tokens_limit"),
            ("proactive_runs", self.daily_proactive_runs_limit, "daily_proactive_runs_limit"),
            (
                "failed_evolutions",
                self.daily_failed_evolutions_limit,
                "daily_failed_evolutions_limit",
            ),
        )
        for key, limit, reason in checks:
            if limit > 0 and int(snapshot.get(key, 0)) >= limit:
                return False, reason, snapshot
        return True, None, snapshot

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

        return await self._execute_context(ctx)

    async def _execute_context(self, ctx: EvolutionContext) -> EvolutionContext:
        """Execute a pre-built context through the evolution pipeline."""
        logger.info(
            "pipeline.start",
            request_id=ctx.request_id,
            source=ctx.request.source.value,
            request=ctx.request.user_request[:120],
            dry_run=ctx.request.dry_run,
        )

        await self.event_reporter.post_event(ctx)
        ctx = await self._run_state_machine(ctx)
        await self.event_reporter.post_event(ctx)

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
            self._purpose_synced = await self.event_reporter.post_purpose(self.purpose)

        while self._running:
            try:
                backend_ready = await self.event_reporter.is_backend_available()
                if not backend_ready:
                    logger.warning(
                        "control_plane.unavailable",
                        retry_seconds=min(interval, _CONTROL_PLANE_RETRY_SECONDS),
                    )
                    await asyncio.sleep(min(interval, _CONTROL_PLANE_RETRY_SECONDS))
                    continue

                if self.purpose and not self._purpose_synced:
                    self._purpose_synced = await self.event_reporter.post_purpose(self.purpose)
                    if self._purpose_synced:
                        logger.info(
                            "purpose.synced_to_backend",
                            version=self.purpose.version,
                            identity=self.purpose.identity.name,
                        )

                await self._refresh_runtime_llm_config()
                await self._refresh_runtime_guardrails()
                await self._publish_usage_snapshot()

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
                        backlog_probe = await self._probe_backlog_state()
                        actionable_backlog_item = (
                            backlog_probe.actionable_item if backlog_probe else None
                        )
                        if actionable_backlog_item is not None:
                            logger.info(
                                "proactive.backlog_pending_trigger",
                                item_id=actionable_backlog_item.id,
                                task_key=actionable_backlog_item.task_key,
                                status=actionable_backlog_item.status.value,
                            )
                            should_run_proactive = True
                        elif (
                            backlog_probe
                            and backlog_probe.is_stalled
                            and elapsed >= _BACKLOG_TRANSIENT_RETRY_SECONDS
                        ):
                            blocked_item = backlog_probe.blocked_frontier_item
                            logger.info(
                                "proactive.backlog_stalled_trigger",
                                task_key=blocked_item.task_key if blocked_item else None,
                                status=blocked_item.status.value if blocked_item else None,
                            )
                            should_run_proactive = True

                        # Bootstrap shortcut: if no apps exist yet and it's been at
                        # least 1 minute since last run, trigger immediately so the
                        # desktop populates without waiting a full hour.
                        apps = None if should_run_proactive else await self.event_reporter.fetch_apps()
                        if apps == []:
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
        """Plan or refresh the proactive backlog, then execute one task from it."""
        async with self._evolution_semaphore:
            budget_ok, budget_reason, snapshot = self._proactive_budget_status()
            if not budget_ok:
                logger.warning(
                    "proactive.safe_mode_budget_exhausted",
                    reason=budget_reason,
                    llm_calls=snapshot.get("llm_calls", 0),
                    input_tokens=snapshot.get("input_tokens", 0),
                    output_tokens=snapshot.get("output_tokens", 0),
                    proactive_runs=snapshot.get("proactive_runs", 0),
                    failed_evolutions=snapshot.get("failed_evolutions", 0),
                )
                await self._publish_usage_snapshot()
                return True

            self._last_proactive_run = time.time()
            logger.info("proactive.analyzing_purpose", purpose_version=self.purpose.version)

            if not await self.event_reporter.is_backend_available():
                logger.warning("proactive.control_plane_unavailable")
                return False

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
            apps_summary, apps_available, _app_count = await self._fetch_apps_summary()
            if not apps_available:
                logger.warning("proactive.apps_unavailable")
                return False

            existing_backlog = await self.event_reporter.fetch_backlog(
                purpose_version=self.purpose.version,
                include_completed=True,
            )
            if existing_backlog is None:
                logger.warning("proactive.backlog_unavailable")
                return False

            if await self._recover_stale_backlog_items(existing_backlog):
                refreshed_backlog = await self.event_reporter.fetch_backlog(
                    purpose_version=self.purpose.version,
                    include_completed=True,
                )
                if refreshed_backlog is not None:
                    existing_backlog = refreshed_backlog

            backlog_summary = self._format_backlog_for_prompt(existing_backlog)
            purpose_yaml = self.purpose.to_yaml_string()
            cache_input = (
                f"{purpose_yaml}\n---\n{codebase_summary}\n---\n{apps_summary}\n---\n{backlog_summary}"
            )
            current_hash = hashlib.sha256(cache_input.encode()).hexdigest()[:16]
            backlog_items = existing_backlog
            replan_reason = self._backlog_replan_reason(existing_backlog)
            should_refresh_backlog = (
                current_hash != self._last_backlog_hash
                or not existing_backlog
                or replan_reason is not None
            )
            if should_refresh_backlog:
                if replan_reason is not None:
                    logger.info("proactive.backlog_force_replan", reason=replan_reason)
                plan = await self._plan_proactive_backlog(
                    codebase_summary=codebase_summary,
                    apps_summary=apps_summary,
                    backlog_summary=backlog_summary,
                )
                if plan is None:
                    logger.warning("proactive.backlog_plan_failed")
                    return False

                synced_backlog = await self.event_reporter.sync_backlog(
                    purpose_version=self.purpose.version,
                    items=plan.items,
                )
                if synced_backlog is None:
                    logger.warning("proactive.backlog_sync_failed")
                    return False
                backlog_items = synced_backlog
                self._last_backlog_hash = current_hash
                logger.info(
                    "proactive.backlog_synced",
                    purpose_version=self.purpose.version,
                    item_count=len(backlog_items),
                    summary=plan.summary[:160],
                )
            else:
                logger.info("proactive.backlog_cache_hit", hash=current_hash)

            backlog_probe = self._inspect_backlog_items(backlog_items)
            next_item = backlog_probe.actionable_item
            if next_item is None:
                non_terminal_count = backlog_probe.non_terminal_count
                if non_terminal_count == 0:
                    logger.info("proactive.backlog_complete", purpose_version=self.purpose.version)
                elif backlog_probe.blocked_frontier_item is not None:
                    logger.info(
                        "proactive.backlog_stalled",
                        purpose_version=self.purpose.version,
                        task_key=backlog_probe.blocked_frontier_item.task_key,
                        status=backlog_probe.blocked_frontier_item.status.value,
                    )
                else:
                    logger.info(
                        "proactive.backlog_waiting",
                        purpose_version=self.purpose.version,
                        non_terminal_count=non_terminal_count,
                    )
                return True

            logger.info(
                "proactive.executing_backlog_item",
                item_id=next_item.id,
                task_key=next_item.task_key,
                task_type=next_item.task_type.value,
                status=next_item.status.value,
            )
            success = await self._execute_backlog_item(next_item)
            self.usage_tracker.record_proactive_run(
                success=success,
                task_key=next_item.task_key,
            )
            await self._publish_usage_snapshot()
            if success:
                self._last_backlog_hash = ""
            return success

    async def _plan_proactive_backlog(
        self,
        codebase_summary: str,
        apps_summary: str,
        backlog_summary: str,
    ) -> BacklogPlannerResponse | None:
        """Ask the fast model for a small persistent roadmap aligned to Purpose."""
        purpose_context = self.purpose.to_prompt_context()

        planning_prompt = f"""You are the proactive roadmap planner for a self-evolving software system.

Your job is to maintain a SMALL persistent backlog for the current Purpose.
You are not choosing a single next request anymore. You are producing an ordered
set of tasks that can be executed across multiple autonomous runs.

{purpose_context}

## Current Codebase Summary
{codebase_summary}

## Existing Apps
{apps_summary}

## Current Persisted Backlog
{backlog_summary}

## Planning Rules
- Return the FULL desired backlog for the current Purpose, not just one task
- Keep the backlog small: 0 to 5 tasks maximum
- Use stable snake_case task_key values when the task intent remains the same
- Each task must be small enough for a single code-generation cycle, usually 3-5 files
- Use priority=high for broken user-visible journeys, missing runtime contracts, or safety fixes
- Use priority=normal for the next core product slice
- Use priority=low for polish, exports, and secondary improvements
- Prefer a vertical slice that becomes visible to the user over backend-only scaffolding
- If at least one business app exists, deepen it before inventing another app
- Do NOT propose System Monitor, Evolution Monitor, Health Monitor, or other meta-apps
  unless the Purpose explicitly requires that domain
- The desktop shell already exists in `frontend/src/App.tsx` and `frontend/src/App.css`.
  Product apps must run inside that shell via `frontend/src/components/AppViewer.tsx`.
- The desktop's system windows and onboarding surfaces are platform capabilities. Do not
  remove Chat, Cost, Settings, Health, Timeline, Purpose, Tasks, Database, or Inceptions
  while planning product work.
- Frontend app slices should live under `frontend/src/apps/<app-slug>/` and expose a default
  component from `frontend/src/apps/<app-slug>/index.ts` or `index.tsx`.
- The repository map is the source of truth for existing frontend app module roots. Reuse the
  exact path it reports for an app; never invent a sibling root that differs only by case,
  camelCase, spacing, or hyphenation.
- Use a stable slug of the desktop app name as the frontend module key, for example
  `Competitive Intelligence` -> `competitive-intelligence`.
- If the repository map reports a path conflict for an app module root, plan a consolidation
  or stabilization slice before deepening that app further.
- Do not replace the shell itself when planning or executing product-app work.
- If a mounted app already has a frontend surface, prioritize keeping its backend contract
  live with safe empty-state responses over adding more scaffolding behind broken endpoints.
- If no business app exists yet, the first actionable task must use task_type=create_app
- task_type=create_app means: register the app shell and then execute the first code slice
- task_type=evolve means: improve an existing app or add the next thin slice
- Mark status=done only when the codebase/apps clearly satisfy the task already
- Mark status=blocked only when a dependency or repeated failure truly prevents progress
- When a task is blocked, set blocked_reason and shrink later tasks accordingly
- Prefer at least one independent follow-up task when possible, so the backlog can keep moving
  if another task enters retry cooldown or becomes blocked
- If a task is already blocked in the persisted backlog, do not reopen it as pending with the
  same task_key unless its scope materially changes or the blocking dependency is gone
- Include acceptance criteria that can be validated after the cycle
- Use depends_on with task_key references when a task must wait on another one

## App Structure Reminder
- App: desktop-visible product surface with a concrete goal
- Feature: user-visible behavior inside an app
- Capability: internal system ability that supports features

## Response Guidance
- If the Purpose is fully satisfied, return an empty items list
- For create_app tasks, app_spec is required
- For evolve tasks, app_spec must be null
- execution_request must describe ONLY the next small slice to build
"""

        try:
            return await self.provider.generate_structured(
                system_prompt=(
                    "You are a senior software architect maintaining a persistent, "
                    "incremental backlog for an autonomous software system."
                ),
                user_prompt=planning_prompt,
                response_model=BacklogPlannerResponse,
                max_tokens=2500,
                model_override="fast",
            )
        except Exception as exc:
            logger.warning("proactive.backlog_planner_error", error=str(exc))
            return None

    def _format_backlog_for_prompt(self, items: list[BacklogItem]) -> str:
        """Render backlog items into a compact planner-friendly summary."""
        if not items:
            return "(No persisted backlog yet)"

        ordered = sorted(items, key=lambda item: (item.sequence, item.created_at or datetime.min))
        lines: list[str] = []
        for item in ordered:
            deps = ", ".join(item.depends_on) if item.depends_on else "none"
            lines.append(
                f"- [{item.status.value}] {item.task_key} (seq={item.sequence}, priority={item.priority.value}, "
                f"attempts={item.attempt_count}, deps={deps}) :: {item.title}"
            )
            if item.description:
                lines.append(f"  desc: {item.description[:220]}")
            if item.last_error:
                lines.append(f"  last_error: {item.last_error[:220]}")
            if item.blocked_reason:
                lines.append(f"  blocked_reason: {item.blocked_reason[:220]}")
            if item.failure_streak:
                lines.append(f"  failure_streak: {item.failure_streak}")
            if item.retry_after:
                lines.append(f"  retry_after: {item.retry_after.isoformat()}")
        return "\n".join(lines)

    def _select_next_backlog_item(self, items: list[BacklogItem]) -> BacklogItem | None:
        """Pick the next actionable backlog item, preferring resumed work and ready retries."""
        return self._inspect_backlog_items(items).actionable_item

    def _inspect_backlog_items(self, items: list[BacklogItem]) -> BacklogProbeState:
        """Summarize whether the backlog can advance or is stalled on blocked work."""
        state = BacklogProbeState()
        if not items:
            return state

        now = datetime.now(timezone.utc)
        ordered = sorted(items, key=lambda item: self._backlog_sort_key(item))
        completed_keys = {
            item.task_key
            for item in ordered
            if item.status == BacklogTaskStatus.DONE
        }
        blocked_keys = {
            item.task_key
            for item in ordered
            if item.status == BacklogTaskStatus.BLOCKED
        }

        for item in ordered:
            if item.status in {BacklogTaskStatus.DONE, BacklogTaskStatus.ABANDONED}:
                continue

            state.non_terminal_count += 1
            dependencies_satisfied = all(dep in completed_keys for dep in item.depends_on)
            daily_task_attempt_limit = self._daily_task_attempt_limit()
            task_attempt_cap_reached = (
                daily_task_attempt_limit > 0
                and self._task_attempts_today(item.task_key) >= daily_task_attempt_limit
            )

            if state.blocked_frontier_item is None:
                if item.status == BacklogTaskStatus.BLOCKED:
                    state.blocked_frontier_item = item
                elif any(dep in blocked_keys for dep in item.depends_on):
                    state.blocked_frontier_item = item

            if state.actionable_item is not None:
                continue
            if item.status not in {BacklogTaskStatus.IN_PROGRESS, BacklogTaskStatus.PENDING}:
                continue
            if not dependencies_satisfied:
                continue
            if task_attempt_cap_reached:
                continue
            if item.retry_after and item.retry_after > now:
                continue
            state.actionable_item = item

        return state

    def _backlog_replan_reason(self, items: list[BacklogItem]) -> str | None:
        """Return a reason when the persisted roadmap should be recomputed immediately."""
        state = self._inspect_backlog_items(items)
        if state.is_stalled and state.blocked_frontier_item is not None:
            return (
                "blocked_frontier:"
                f"{state.blocked_frontier_item.task_key}:{state.blocked_frontier_item.status.value}"
            )
        return None

    def _backlog_sort_key(self, item: BacklogItem) -> tuple[int, int, int, datetime]:
        """Order backlog work by runtime readiness, then planner intent."""
        status_rank = 0 if item.status == BacklogTaskStatus.IN_PROGRESS else 1
        priority_value = (
            item.priority.value
            if isinstance(item.priority, BacklogTaskPriority)
            else str(item.priority)
        )
        priority_rank = _BACKLOG_PRIORITY_ORDER.get(priority_value, 1)
        created_at = item.created_at or datetime.min.replace(tzinfo=timezone.utc)
        return (status_rank, priority_rank, item.sequence, created_at)

    async def _peek_actionable_backlog_item(self) -> BacklogItem | None:
        """Return the next actionable backlog item without mutating planner state."""
        backlog_probe = await self._probe_backlog_state()
        return backlog_probe.actionable_item if backlog_probe else None

    async def _probe_backlog_state(self) -> BacklogProbeState | None:
        """Fetch the current backlog and summarize whether it can advance."""
        if not self.purpose:
            return None

        backlog_items = await self.event_reporter.fetch_backlog(
            purpose_version=self.purpose.version,
            include_completed=True,
        )
        if backlog_items is None:
            logger.debug("proactive.backlog_probe_unavailable")
            return None

        return self._inspect_backlog_items(backlog_items)

    async def _execute_backlog_item(self, item: BacklogItem) -> bool:
        """Execute one persisted backlog item and update its state."""
        self.usage_tracker.record_task_attempt(item.task_key)
        await self._publish_usage_snapshot()
        request_text = self._build_backlog_request(item)
        ctx = create_context(
            request_text,
            dry_run=False,
            source=EvolutionSource.MONITOR,
        )

        now = datetime.now(timezone.utc).isoformat()
        await self.event_reporter.update_backlog_item(
            item.id,
            {
                "status": BacklogTaskStatus.IN_PROGRESS.value,
                "last_request_id": ctx.request_id,
                "attempt_count": item.attempt_count + 1,
                "retry_after": None,
                "blocked_reason": None,
                "last_attempted_at": now,
                "started_at": now,
                "completed_at": None,
            },
        )

        created_app_id: str | None = None
        if item.task_type == BacklogTaskType.CREATE_APP:
            if item.app_spec is None:
                await self._mark_backlog_item_failed(
                    item,
                    request_id=ctx.request_id,
                    error_message="Planner returned create_app without app_spec",
                )
                return False
            created_app_id = await self._ensure_app_registered(item.app_spec)
            if created_app_id is None:
                await self._mark_backlog_item_failed(
                    item,
                    request_id=ctx.request_id,
                    error_message=f"Failed to register app '{item.app_spec.name}' before evolution",
                )
                return False

        ctx = await self._execute_context(ctx)

        if created_app_id and ctx.status == EvolutionStatus.COMPLETED:
            await self.event_reporter.update_app(created_app_id, {"status": "active"})

        await self._finalize_backlog_item(item, ctx)
        logger.info(
            "proactive.backlog_item_complete",
            item_id=item.id,
            task_key=item.task_key,
            request_id=ctx.request_id,
            status=ctx.status.value,
        )
        return ctx.status == EvolutionStatus.COMPLETED

    def _build_backlog_request(self, item: BacklogItem) -> str:
        """Convert a backlog item into a concrete evolution request."""
        acceptance = ""
        if item.acceptance_criteria:
            acceptance = "\nAcceptance criteria:\n" + "\n".join(
                f"- {criterion}" for criterion in item.acceptance_criteria
            )
        return (
            f"[Proactive Task: {item.title}] {item.execution_request.strip()}"
            f"{acceptance}"
        ).strip()

    async def _ensure_app_registered(self, app_spec: BacklogAppSpec) -> str | None:
        """Create the app shell if needed and return its app ID."""
        existing_apps = await self.event_reporter.fetch_apps()
        if existing_apps:
            for app in existing_apps:
                if app.get("name", "").strip().lower() == app_spec.name.strip().lower():
                    return app.get("id")

        capability_ids: list[str] = []
        for capability in app_spec.capabilities:
            cap_id = await self.event_reporter.create_capability(
                {
                    "name": capability.name,
                    "description": capability.description,
                    "is_background": capability.is_background,
                }
            )
            if cap_id:
                capability_ids.append(cap_id)

        features_payload = [
            {
                "name": feature.name,
                "description": feature.description,
                "user_facing_description": feature.description,
                "capability_ids": capability_ids,
            }
            for feature in app_spec.features
        ]

        return await self.event_reporter.create_app(
            {
                "name": app_spec.name,
                "icon": app_spec.icon,
                "goal": app_spec.goal,
                "status": "building",
                "features": features_payload,
                "capability_ids": capability_ids,
                "metadata_json": {
                    "frontend_entry": _frontend_entry_key(app_spec.name),
                },
            }
        )

    async def _mark_backlog_item_failed(
        self,
        item: BacklogItem,
        request_id: str,
        error_message: str,
    ) -> None:
        """Persist a failed backlog attempt that never reached the pipeline."""
        attempt_count = item.attempt_count + 1
        payload = self._build_backlog_failure_payload(
            item=item,
            request_id=request_id,
            error_message=error_message,
            attempt_count=attempt_count,
        )
        await self.event_reporter.update_backlog_item(
            item.id,
            payload,
        )

    async def _finalize_backlog_item(self, item: BacklogItem, ctx: EvolutionContext) -> None:
        """Persist the outcome of a backlog task after the pipeline finishes."""
        if ctx.status == EvolutionStatus.COMPLETED:
            await self.event_reporter.update_backlog_item(
                item.id,
                {
                    "status": BacklogTaskStatus.DONE.value,
                    "last_request_id": ctx.request_id,
                    "attempt_count": item.attempt_count + 1,
                    "failure_streak": 0,
                    "last_error": None,
                    "blocked_reason": None,
                    "retry_after": None,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return

        error_message = self._summarize_backlog_failure(ctx)
        attempt_count = item.attempt_count + 1
        payload = self._build_backlog_failure_payload(
            item=item,
            request_id=ctx.request_id,
            error_message=error_message,
            attempt_count=attempt_count,
        )
        await self.event_reporter.update_backlog_item(
            item.id,
            payload,
        )

    def _summarize_backlog_failure(self, ctx: EvolutionContext) -> str:
        """Extract a compact failure summary for backlog persistence."""
        parts: list[str] = []
        if ctx.error:
            parts.append(ctx.error)
        if ctx.validation_result and ctx.validation_result.errors:
            parts.extend(ctx.validation_result.errors[:5])
        if not parts:
            return "Evolution failed without a structured error message"
        return "\n".join(parts)

    def _build_backlog_failure_payload(
        self,
        *,
        item: BacklogItem,
        request_id: str,
        error_message: str,
        attempt_count: int,
    ) -> dict[str, str | int | None]:
        """Compute retry state for a failed backlog attempt."""
        now = datetime.now(timezone.utc)
        transient = self._is_transient_backlog_failure(error_message)

        if transient:
            next_status = (
                BacklogTaskStatus.BLOCKED
                if attempt_count >= _BACKLOG_BLOCK_AFTER_TOTAL_ATTEMPTS
                else BacklogTaskStatus.PENDING
            )
            failure_streak = 0
            retry_after = (
                None
                if next_status == BacklogTaskStatus.BLOCKED
                else now + timedelta(seconds=_BACKLOG_TRANSIENT_RETRY_SECONDS)
            )
        else:
            failure_streak = item.failure_streak + 1
            next_status = (
                BacklogTaskStatus.BLOCKED
                if failure_streak >= _BACKLOG_BLOCK_AFTER_FAILURES
                else BacklogTaskStatus.PENDING
            )
            retry_after = (
                None
                if next_status == BacklogTaskStatus.BLOCKED
                else now + timedelta(seconds=self._structural_retry_delay_seconds(failure_streak))
            )

        return {
            "status": next_status.value,
            "last_request_id": request_id,
            "attempt_count": attempt_count,
            "failure_streak": failure_streak,
            "last_error": error_message[:2000],
            "blocked_reason": error_message[:500]
            if next_status == BacklogTaskStatus.BLOCKED
            else None,
            "retry_after": retry_after.isoformat() if retry_after else None,
            "started_at": None,
            "completed_at": None,
        }

    def _structural_retry_delay_seconds(self, failure_streak: int) -> int:
        """Back off more aggressively for likely code defects than transient infra blips."""
        return _BACKLOG_STRUCTURAL_RETRY_SCHEDULE_SECONDS.get(
            failure_streak,
            max(_BACKLOG_STRUCTURAL_RETRY_SCHEDULE_SECONDS.values()),
        )

    def _is_transient_backlog_failure(self, error_message: str) -> bool:
        """Detect retryable infra/provider issues that should not immediately block product work."""
        return any(pattern.search(error_message) for pattern in _TRANSIENT_BACKLOG_ERROR_PATTERNS)

    async def _recover_stale_backlog_items(self, items: list[BacklogItem]) -> bool:
        """Release abandoned in-progress leases so the backlog can continue advancing."""
        if not items:
            return False

        now = datetime.now(timezone.utc)
        recovered = False
        for item in items:
            if item.status != BacklogTaskStatus.IN_PROGRESS or item.started_at is None:
                continue

            age_seconds = (now - item.started_at).total_seconds()
            if age_seconds < _BACKLOG_STALE_IN_PROGRESS_SECONDS:
                continue

            retry_after = now + timedelta(seconds=_BACKLOG_TRANSIENT_RETRY_SECONDS)
            error_message = (
                "Recovered stale in_progress task after "
                f"{int(age_seconds // 60)} minutes without completion"
            )
            await self.event_reporter.update_backlog_item(
                item.id,
                {
                    "status": BacklogTaskStatus.PENDING.value,
                    "attempt_count": item.attempt_count,
                    "failure_streak": item.failure_streak,
                    "last_error": error_message,
                    "blocked_reason": None,
                    "retry_after": retry_after.isoformat(),
                    "started_at": None,
                    "completed_at": None,
                    "last_request_id": item.last_request_id,
                    "last_attempted_at": (item.last_attempted_at or item.started_at).isoformat(),
                },
            )
            logger.warning(
                "proactive.backlog_stale_recovered",
                item_id=item.id,
                task_key=item.task_key,
                age_minutes=int(age_seconds // 60),
            )
            recovered = True

        return recovered

    async def _fetch_apps_summary(self) -> tuple[str, bool, int]:
        """Fetch the list of existing apps from the backend for the proactive analyzer."""
        try:
            apps = await self.event_reporter.fetch_apps()
            if apps is None:
                return "(Apps unavailable — backend API did not respond)", False, 0
            if not apps:
                return "(No apps created yet)", True, 0

            lines = []
            for app in apps:
                feat_count = app.get("feature_count", 0)
                cap_count = app.get("capability_count", 0)
                lines.append(
                    f"- {app.get('icon', '')} **{app.get('name', '?')}** "
                    f"[{app.get('status', '?')}] — {app.get('goal', 'no goal')} "
                    f"({feat_count} features, {cap_count} capabilities)"
                )
            lines.append("")
            lines.append("There is already at least one registered business app. Do not treat the system as app-less.")
            return "\n".join(lines), True, len(apps)
        except Exception as exc:
            logger.debug("proactive.fetch_apps_error", error=str(exc))
            return "(Apps unavailable — backend API did not respond)", False, 0

    async def _build_codebase_summary(self, app_path: Path) -> str:
        """Build a quick summary of the codebase for the proactive analyzer.

        Scans key files (routes, models, components) to understand what's implemented.
        """
        summary_parts: list[str] = []

        try:
            repo_map = build_repo_map(app_path)
            summary_parts.append(repo_map.to_context_string(max_chars=4000))
        except Exception as exc:
            logger.debug("proactive.repo_map_summary_failed", error=str(exc))

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
                self._last_backlog_hash = ""

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
