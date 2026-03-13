"""Code Validator Agent — tests generated code in an isolated sandbox.

Responsibilities (MAPE-K: Execute + Knowledge):
- Copy generated files into a sandbox environment
- Run a three-stage validation pipeline:
  1. Static analysis (linting)
  2. Build test (Docker build)
  3. Integration test (full stack up + test suite)
- Measure empirical risk (error rate, test failures)
- Return PASS/FAIL with detailed feedback
- On failure, provide actionable suggestions for the Generator to retry
"""

from engine.agents.base import BaseAgent
from engine.context import EvolutionContext
from engine.models.evolution import EvolutionStatus, ValidationResult
from engine.sandbox.base import BaseSandbox


class CodeValidatorAgent(BaseAgent):
    """Validates generated code by running it in an isolated sandbox."""

    def __init__(self, sandbox: BaseSandbox, **kwargs) -> None:
        super().__init__(**kwargs)
        self.sandbox = sandbox

    @property
    def name(self) -> str:
        return "validator"

    async def _execute(self, ctx: EvolutionContext) -> EvolutionContext:
        """Run the generated code through the sandbox validation pipeline."""
        if not ctx.generated_files:
            return ctx.fail("No generated files to validate — Generator must run first")

        self.logger.info(
            "validation.start",
            num_files=len(ctx.generated_files),
            retry_count=ctx.retry_count,
        )

        try:
            result = await self.sandbox.run_tests(ctx)
        except Exception as exc:
            self.logger.error("sandbox.error", error=str(exc))
            result = ValidationResult(
                passed=False,
                logs=str(exc),
                errors=[f"Sandbox execution failed: {exc}"],
            )
        finally:
            # Always clean up sandbox resources
            try:
                await self.sandbox.cleanup()
            except Exception as cleanup_exc:
                self.logger.warning("sandbox.cleanup_error", error=str(cleanup_exc))

        self.logger.info(
            "validation.result",
            passed=result.passed,
            risk_score=result.risk_score,
            num_errors=len(result.errors),
        )

        if result.passed:
            return ctx.model_copy(
                update={
                    "validation_result": result,
                    "status": EvolutionStatus.DEPLOYING,
                }
            )

        # Validation failed — check if retries are available
        if ctx.can_retry:
            self.logger.warning(
                "validation.retry",
                retry_count=ctx.retry_count + 1,
                max_retries=ctx.max_retries,
            )
            return ctx.model_copy(
                update={
                    "validation_result": result,
                    "status": EvolutionStatus.GENERATING,  # Send back to Generator
                    "retry_count": ctx.retry_count + 1,
                }
            )

        # No retries left — fail permanently
        return ctx.model_copy(
            update={
                "validation_result": result,
                "status": EvolutionStatus.FAILED,
                "error": f"Validation failed after {ctx.max_retries} retries: "
                + "; ".join(result.errors[:3]),
            }
        )
