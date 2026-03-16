"""CLI entrypoint for the evolving engine.

Two operating modes:

  TRIGGERED — single evolution driven by an explicit request:
    python -m engine "Add a products CRUD with database table and React component"
    python -m engine --dry-run "Add user authentication"

  CONTINUOUS — autonomous MAPE-K loop (monitor → analyze → plan → execute):
    python -m engine --continuous
    python -m engine --continuous --interval 30
    python -m engine --continuous --dry-run   # observe only, never deploy

In continuous mode the engine runs indefinitely until interrupted (Ctrl+C).
It polls the Managed System, detects anomalies, and autonomously generates
and deploys fixes without any human input.
"""

import argparse
import asyncio
import signal
import sys

import structlog

from engine.orchestrator import Orchestrator

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

logger = structlog.get_logger()


def _print_summary(ctx) -> None:
    """Print a human-readable summary of a completed evolution context."""
    print("\n" + "=" * 60)
    print(f"  Evolution ID : {ctx.request_id}")
    print(f"  Status       : {ctx.status.value.upper()}")
    print(f"  Source       : {ctx.request.source.value}")
    print(f"  Target       : {ctx.request.target.value}")
    print(f"  Retries      : {ctx.retry_count}")

    if ctx.plan:
        print(f"  Plan         : {ctx.plan.summary}")
        print(f"  Risk Level   : {ctx.plan.risk_level}")
        print(f"  Files        : {len(ctx.generated_files)}")

    if ctx.validation_result:
        outcome = "PASSED" if ctx.validation_result.passed else "FAILED"
        print(f"  Validation   : {outcome}  (risk={ctx.validation_result.risk_score:.2f})")

    if ctx.deployment_result:
        outcome = "SUCCESS" if ctx.deployment_result.success else "FAILED"
        print(f"  Deployment   : {outcome}")
        if ctx.deployment_result.commit_sha:
            print(f"  Commit       : {ctx.deployment_result.commit_sha[:8]}")
        if ctx.deployment_result.branch:
            print(f"  Branch       : {ctx.deployment_result.branch}")

    if ctx.error:
        print(f"  Error        : {ctx.error}")

    print("=" * 60)

    if ctx.history:
        print("\n  Audit Trail:")
        for event in ctx.history:
            ts = event.timestamp.strftime("%H:%M:%S")
            detail = f": {event.details}" if event.details else ""
            print(f"    [{ts}] {event.agent}.{event.action} → {event.status}{detail}")
        print()


async def _run_triggered(args: argparse.Namespace) -> int:
    """Single evolution triggered by a CLI request."""
    orchestrator = Orchestrator()
    ctx = await orchestrator.run(args.request, dry_run=args.dry_run)
    _print_summary(ctx)
    return 0 if ctx.status.value == "completed" else 1


async def _run_continuous(args: argparse.Namespace) -> int:
    """Autonomous MAPE-K loop — runs until interrupted."""
    from engine.config import settings

    if args.interval:
        settings.monitor_interval_seconds = args.interval

    orchestrator = Orchestrator()

    # Graceful shutdown on SIGTERM (Docker stop) or SIGINT (Ctrl+C)
    loop = asyncio.get_running_loop()

    def _shutdown():
        logger.info("shutdown.signal_received")
        orchestrator.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass  # Windows

    print(f"\n  Continuous MAPE-K loop started")
    print(f"  Monitor URL   : {settings.monitor_url}")
    print(f"  Interval      : {settings.monitor_interval_seconds}s")
    print(f"  Dry run       : {args.dry_run}")

    if orchestrator.genesis:
        print(f"  Genesis       : v{orchestrator.genesis.version}")
    if orchestrator.purpose:
        print(f"  Purpose       : v{orchestrator.purpose.version} — {orchestrator.purpose.identity.name}")

    print(f"  Press Ctrl+C to stop\n")

    await orchestrator.run_continuous()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Self-Evolving Software Engine — autonomous MAPE-K evolution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Mutually exclusive: triggered (positional) vs continuous (--continuous)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "request",
        nargs="?",
        type=str,
        help="Natural language evolution request (triggered mode)",
    )
    mode.add_argument(
        "--continuous",
        action="store_true",
        default=False,
        help="Run the autonomous MAPE-K monitoring loop indefinitely",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate changes without deploying (both modes)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Override monitor interval (continuous mode only)",
    )

    args = parser.parse_args()

    if args.continuous:
        exit_code = asyncio.run(_run_continuous(args))
    else:
        if not args.request:
            parser.error("A request string is required in triggered mode.")
        exit_code = asyncio.run(_run_triggered(args))

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
