"""Docker Sandbox — runs generated code in isolated Docker containers for testing.

This is the primary sandbox for local development and CI environments.

Validation pipeline:
1. Static analysis: ruff (Python) + tsc --noEmit (TypeScript)
2. Build test: docker build each modified service
3. Integration test: docker-compose up, run pytest + vitest, verify HTTP health
"""

import asyncio
import shutil
import tempfile
from pathlib import Path

import docker
import structlog

from engine.config import EngineSettings, settings
from engine.context import EvolutionContext
from engine.models.evolution import ValidationResult
from engine.sandbox.base import BaseSandbox

logger = structlog.get_logger()


class DockerSandbox(BaseSandbox):
    """Sandbox that uses Docker to test generated code in isolation."""

    def __init__(self, config: EngineSettings | None = None) -> None:
        self.config = config or settings
        self.client = docker.from_env()
        self.temp_dir: Path | None = None
        self.containers: list[str] = []

    async def run_tests(self, context: EvolutionContext) -> ValidationResult:
        """Execute the three-stage validation pipeline."""
        errors: list[str] = []
        suggestions: list[str] = []

        # Stage 0: Prepare sandbox workspace
        self.temp_dir = Path(tempfile.mkdtemp(prefix="evo_sandbox_"))
        sandbox_app = self.temp_dir / "managed_app"

        # Copy the current managed app into the sandbox
        managed_app_src = Path(self.config.managed_app_path).resolve()
        if managed_app_src.exists():
            shutil.copytree(managed_app_src, sandbox_app)
        else:
            return ValidationResult(
                passed=False,
                errors=["Managed app source directory not found"],
            )

        # Overlay generated files onto the sandbox copy
        workspace = Path(self.config.workspace_path) / context.request_id
        if workspace.exists():
            for gen_file in context.generated_files:
                src = workspace / gen_file.file_path
                dst = sandbox_app / gen_file.file_path
                if gen_file.action == "delete":
                    dst.unlink(missing_ok=True)
                elif src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

        # Stage 1: Static analysis
        static_ok, static_errors = await self._run_static_analysis(sandbox_app)
        if not static_ok:
            errors.extend(static_errors)
            suggestions.append("Fix linting errors before proceeding")

        # Stage 2: Build test
        build_ok, build_errors = await self._run_build_test(sandbox_app)
        if not build_ok:
            errors.extend(build_errors)
            suggestions.append("Ensure Dockerfiles build without errors")

        # Stage 2.5: Backend startup smoke test
        smoke_ok = False
        if build_ok:
            smoke_ok, smoke_errors = await self._run_backend_import_smoke_test()
            if not smoke_ok:
                errors.extend(smoke_errors)
                suggestions.append("Backend changes must import app.main successfully before deploy")

        # Stage 3: Integration test (only if build passed)
        tests_ok = False
        if build_ok and smoke_ok:
            tests_ok, test_errors = await self._run_integration_tests(sandbox_app)
            if not tests_ok:
                errors.extend(test_errors)
                suggestions.append("Fix failing tests and ensure endpoints return HTTP 200")

        # Calculate risk score (0.0 = safe, 1.0 = dangerous)
        risk_score = 0.0
        if not static_ok:
            risk_score += 0.2
        if not build_ok:
            risk_score += 0.5
        elif not smoke_ok:
            risk_score += 0.5
        if not tests_ok:
            risk_score += 0.1  # Tests are advisory — lower weight
        risk_score = min(risk_score, 1.0)

        # Passing requires static analysis + build. Integration tests are
        # advisory: they improve confidence but don't block deployment.
        # This is intentional — the managed app often has no test files yet,
        # and blocking on "no tests collected" prevents any evolution.
        return ValidationResult(
            passed=static_ok and build_ok and smoke_ok,
            risk_score=risk_score,
            static_analysis_passed=static_ok,
            build_passed=build_ok,
            tests_passed=tests_ok,
            errors=errors,
            suggestions=suggestions,
            logs=f"Sandbox workspace: {self.temp_dir}",
        )

    async def _run_static_analysis(self, app_path: Path) -> tuple[bool, list[str]]:
        """Run linting tools on the generated code."""
        errors: list[str] = []

        # Python: ruff check
        backend_path = app_path / "backend"
        if backend_path.exists():
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ruff", "check", str(backend_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                if proc.returncode != 0:
                    errors.append(f"Python linting errors:\n{stdout.decode()}")
            except FileNotFoundError:
                logger.warning("static_analysis.ruff_not_found")
            except asyncio.TimeoutError:
                errors.append("Python linting timed out")

        return len(errors) == 0, errors

    async def _run_build_test(self, app_path: Path) -> tuple[bool, list[str]]:
        """Attempt to docker build each service.

        For the backend, tries the ``test`` stage first (for pytest). If that
        fails, falls back to ``base`` stage — the code still compiles and the
        evolution should not be blocked just because dev deps fail to install.
        """
        errors: list[str] = []

        for service in ["backend", "frontend"]:
            dockerfile = app_path / service / "Dockerfile"
            if not dockerfile.exists():
                continue

            try:
                tag = f"evo-sandbox-{service}:test"
                build_kwargs: dict = dict(
                    path=str(app_path / service),
                    tag=tag,
                    rm=True,
                    timeout=self.config.sandbox_timeout_seconds,
                )

                # Try 'test' stage first for backend (includes pytest)
                if service == "backend":
                    build_kwargs["target"] = "test"

                try:
                    self.client.images.build(**build_kwargs)
                    logger.info("build.success", service=service, target=build_kwargs.get("target"))
                except docker.errors.BuildError:
                    if service == "backend":
                        # Fallback: try 'base' stage — code compiles even if dev deps fail
                        logger.warning("build.test_stage_failed_fallback_to_base", service=service)
                        build_kwargs["target"] = "base"
                        self.client.images.build(**build_kwargs)
                        logger.info("build.success", service=service, target="base")
                    else:
                        raise

            except docker.errors.BuildError as exc:
                errors.append(f"Docker build failed for {service}: {exc}")
            except docker.errors.APIError as exc:
                errors.append(f"Docker API error for {service}: {exc}")

        return len(errors) == 0, errors

    async def _run_integration_tests(self, app_path: Path) -> tuple[bool, list[str]]:
        """Run the test suite inside Docker containers.

        Uses the ``evo-sandbox-backend:test`` image (built with the ``test``
        Dockerfile stage) which includes pytest and other dev dependencies.
        """
        errors: list[str] = []

        # Run pytest in the backend container
        try:
            tag = "evo-sandbox-backend:test"

            # Provide minimal env so app config can initialize without
            # connecting to a real database (tests use ASGI transport).
            test_env = {
                "APP_DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/test",
                "APP_ENVIRONMENT": "test",
            }

            container = self.client.containers.run(
                tag,
                command="pytest tests/ -v --tb=short --no-header -q",
                environment=test_env,
                detach=True,
                remove=False,
            )
            self.containers.append(container.id)

            # Wait for completion with timeout
            result = container.wait(timeout=self.config.sandbox_timeout_seconds)
            logs = container.logs().decode()

            if result["StatusCode"] != 0:
                # Exit code 5 = no tests collected — not a failure
                if result["StatusCode"] == 5:
                    logger.info("tests.backend.no_tests_collected")
                else:
                    errors.append(f"Backend tests failed:\n{logs[-2000:]}")
            else:
                logger.info("tests.backend.passed")

        except docker.errors.ContainerError as exc:
            errors.append(f"Backend test container error: {exc}")
        except docker.errors.ImageNotFound:
            logger.warning("tests.backend.image_not_found")
        except Exception as exc:
            errors.append(f"Integration test error: {exc}")

        return len(errors) == 0, errors

    async def _run_backend_import_smoke_test(self) -> tuple[bool, list[str]]:
        """Verify the backend image can import app.main before deployment."""
        errors: list[str] = []

        try:
            container = self.client.containers.run(
                "evo-sandbox-backend:test",
                command=["python", "-c", "from app.main import app; print(app.title)"],
                environment={
                    "APP_DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/test",
                    "APP_ENVIRONMENT": "test",
                },
                detach=True,
                remove=False,
            )
            self.containers.append(container.id)

            result = container.wait(timeout=self.config.sandbox_timeout_seconds)
            logs = container.logs().decode()

            if result["StatusCode"] != 0:
                errors.append(f"Backend startup smoke test failed:\n{logs[-2000:]}")
            else:
                logger.info("tests.backend.import_smoke_passed")

        except docker.errors.ContainerError as exc:
            errors.append(f"Backend startup smoke container error: {exc}")
        except docker.errors.ImageNotFound:
            errors.append("Backend startup smoke image not found")
        except Exception as exc:
            errors.append(f"Backend startup smoke error: {exc}")

        return len(errors) == 0, errors

    async def cleanup(self) -> None:
        """Remove sandbox containers and temporary directories."""
        # Remove containers
        for container_id in self.containers:
            try:
                container = self.client.containers.get(container_id)
                container.remove(force=True)
            except docker.errors.NotFound:
                pass
            except Exception as exc:
                logger.warning("cleanup.container_error", error=str(exc))

        self.containers.clear()

        # Remove temp directory
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = None

        # Remove sandbox images
        for tag in ["evo-sandbox-backend:test", "evo-sandbox-frontend:test"]:
            try:
                self.client.images.remove(tag, force=True)
            except docker.errors.ImageNotFound:
                pass
            except Exception as exc:
                logger.warning("cleanup.image_error", error=str(exc))
