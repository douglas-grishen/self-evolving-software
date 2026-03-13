"""AWS CodeBuild Sandbox — runs validation in AWS CodeBuild for production environments.

This sandbox is used when Docker is not available locally (e.g., in the cloud engine).
It triggers an AWS CodeBuild project that builds and tests the generated code.
"""

import time

import boto3
import structlog

from engine.config import EngineSettings, settings
from engine.context import EvolutionContext
from engine.models.evolution import ValidationResult
from engine.sandbox.base import BaseSandbox

logger = structlog.get_logger()


class CodeBuildSandbox(BaseSandbox):
    """Sandbox backed by AWS CodeBuild for cloud-native validation."""

    def __init__(self, config: EngineSettings | None = None) -> None:
        self.config = config or settings
        self.client = boto3.client("codebuild", region_name=self.config.aws_region)
        self.build_id: str | None = None

    async def run_tests(self, context: EvolutionContext) -> ValidationResult:
        """Trigger a CodeBuild build to validate the generated code."""
        try:
            # Start the CodeBuild build
            response = self.client.start_build(
                projectName=f"evo-sandbox-{context.request_id[:8]}",
                sourceTypeOverride="NO_SOURCE",
                buildspecOverride=self._generate_buildspec(context),
                environmentVariablesOverride=[
                    {
                        "name": "EVOLUTION_REQUEST_ID",
                        "value": context.request_id,
                        "type": "PLAINTEXT",
                    },
                ],
            )

            self.build_id = response["build"]["id"]
            logger.info("codebuild.started", build_id=self.build_id)

            # Poll for completion
            result = await self._wait_for_build()

            return result

        except Exception as exc:
            logger.error("codebuild.error", error=str(exc))
            return ValidationResult(
                passed=False,
                errors=[f"CodeBuild sandbox error: {exc}"],
            )

    async def _wait_for_build(self) -> ValidationResult:
        """Poll CodeBuild until the build completes or times out."""
        if not self.build_id:
            return ValidationResult(passed=False, errors=["No build ID available"])

        timeout = self.config.sandbox_timeout_seconds
        elapsed = 0
        poll_interval = 10

        while elapsed < timeout:
            response = self.client.batch_get_builds(ids=[self.build_id])
            builds = response.get("builds", [])

            if not builds:
                return ValidationResult(passed=False, errors=["Build not found"])

            build = builds[0]
            status = build.get("buildStatus", "IN_PROGRESS")

            if status == "SUCCEEDED":
                return ValidationResult(
                    passed=True,
                    risk_score=0.0,
                    logs=f"CodeBuild build {self.build_id} succeeded",
                )
            elif status in ("FAILED", "FAULT", "TIMED_OUT", "STOPPED"):
                phases = build.get("phases", [])
                errors = []
                for phase in phases:
                    if phase.get("phaseStatus") == "FAILED":
                        contexts = phase.get("contexts", [])
                        for ctx in contexts:
                            errors.append(ctx.get("message", "Unknown error"))

                return ValidationResult(
                    passed=False,
                    risk_score=0.8,
                    errors=errors or [f"Build {status}"],
                    logs=f"CodeBuild build {self.build_id} {status}",
                )

            time.sleep(poll_interval)
            elapsed += poll_interval

        return ValidationResult(
            passed=False,
            errors=[f"CodeBuild timed out after {timeout}s"],
        )

    def _generate_buildspec(self, context: EvolutionContext) -> str:
        """Generate an inline buildspec.yml for the CodeBuild project."""
        return """
version: 0.2
phases:
  install:
    runtime-versions:
      python: 3.11
      nodejs: 22
  pre_build:
    commands:
      - echo "Validating evolution $EVOLUTION_REQUEST_ID"
      - cd managed_app/backend && pip install -e ".[dev]"
      - cd managed_app/frontend && npm install
  build:
    commands:
      - cd managed_app/backend && ruff check app/
      - cd managed_app/backend && pytest tests/ -v
      - cd managed_app/frontend && npx tsc --noEmit
  post_build:
    commands:
      - echo "Validation complete"
""".strip()

    async def cleanup(self) -> None:
        """Clean up CodeBuild resources."""
        if self.build_id:
            try:
                self.client.stop_build(id=self.build_id)
            except Exception:
                pass  # Build may have already completed
            self.build_id = None
