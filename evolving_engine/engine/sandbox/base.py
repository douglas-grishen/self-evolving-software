"""BaseSandbox — abstract interface for isolated code testing environments."""

from abc import ABC, abstractmethod

from engine.context import EvolutionContext
from engine.models.evolution import ValidationResult


class BaseSandbox(ABC):
    """Abstract base class for sandbox environments.

    A sandbox:
    - Receives generated code from the evolution context
    - Builds and tests it in an isolated environment
    - Returns a ValidationResult with pass/fail, logs, and risk score
    - Cleans up all resources after testing
    """

    @abstractmethod
    async def run_tests(self, context: EvolutionContext) -> ValidationResult:
        """Execute the validation pipeline in the sandbox.

        Steps:
        1. Prepare the sandbox environment (copy files, set up containers)
        2. Run static analysis (linting)
        3. Run build test (Docker build)
        4. Run integration tests (full stack + test suite)
        5. Return aggregated results
        """

    @abstractmethod
    async def cleanup(self) -> None:
        """Clean up all sandbox resources (containers, temp dirs, networks)."""
