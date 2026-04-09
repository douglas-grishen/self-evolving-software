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
from engine.repo.scanner import canonicalize_frontend_app_key, extract_frontend_app_modules
from engine.runtime_contracts import get_platform_file_contracts
from engine.sandbox.base import BaseSandbox

logger = structlog.get_logger()

_AUTO_MANAGED_PLAN_PATHS = {
    "backend/app/api/v1/__init__.py",
    "backend/app/models/__init__.py",
}

_BACKEND_SHELL_PATHS = {
    "backend/app/main.py",
}

_DESKTOP_SHELL_PATHS = {
    "frontend/src/App.tsx",
    "frontend/src/App.css",
}

_DESKTOP_SHELL_KEYWORDS = (
    "desktop",
    "shell",
    "launcher",
    "menu bar",
    "menubar",
    "window manager",
    "dock",
)

_REQUIRED_DESKTOP_MENU_LABELS = (
    "New Inception",
    "Inceptions",
    "Timeline",
    "Purpose",
    "Tasks",
    "Chat",
    "Architecture",
    "Database",
    "Cost",
    "Health",
    "Settings",
)

_REQUIRED_PLATFORM_FILES = {
    "backend/app/main.py": (
        "app.include_router(v1_router)",
        '@app.get("/health")',
    ),
    "frontend/src/App.tsx": (
        "toggle(\"chat\")",
        "toggle(\"cost\")",
        'title="✦ Chat with the System"',
        'title="Cost & Usage"',
    ),
    "frontend/src/components/AppViewer.tsx": (
        "getDesktopAppComponent",
        "<DesktopAppComponent app={app} />",
    ),
    "frontend/src/components/ChatView.tsx": (
        'fetch("/api/v1/chat"',
        "JSON.stringify({ messages: history })",
    ),
    "frontend/src/components/CostView.tsx": (
        "Cost & Usage",
        "Spend Telemetry",
    ),
    "backend/app/api/v1/chat.py": (
        'APIRouter(prefix="/chat"',
        '@router.post("")',
        "messages: list[ChatMessage]",
    ),
}

_SANDBOX_COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".next",
    "dist",
    "build",
    "*.pyc",
    "*.pyo",
)


def _request_allows_desktop_shell_changes(request_text: str) -> bool:
    text = request_text.lower()
    return any(keyword in text for keyword in _DESKTOP_SHELL_KEYWORDS)


def _validate_platform_contract_files(
    app_path: Path,
    request_text: str,
    contracts_path: Path | None = None,
) -> list[str]:
    """Validate framework-owned platform capabilities that product work must preserve."""
    errors: list[str] = []
    allows_shell_change = _request_allows_desktop_shell_changes(request_text)

    for relative_path, markers in _REQUIRED_PLATFORM_FILES.items():
        file_path = app_path / relative_path
        if not file_path.exists():
            errors.append(
                f"Platform contract violation: required framework file is missing: {relative_path}"
            )
            continue

        content = file_path.read_text(encoding="utf-8")
        effective_markers = markers
        if allows_shell_change and relative_path == "frontend/src/App.tsx":
            effective_markers = ()

        missing_markers = [marker for marker in effective_markers if marker not in content]
        if missing_markers:
            preview = ", ".join(missing_markers[:4])
            errors.append(
                "Platform contract violation: "
                f"{relative_path} is missing required framework markers: {preview}"
            )

        if not allows_shell_change and relative_path == "frontend/src/App.tsx":
            missing_labels = [
                label for label in _REQUIRED_DESKTOP_MENU_LABELS if label not in content
            ]
            if missing_labels:
                preview = ", ".join(missing_labels[:6])
                errors.append(
                    "Desktop shell must preserve core system windows and menu items: "
                    f"{preview}"
                )

    for contract in get_platform_file_contracts(contracts_path):
        trigger_path = app_path / contract.trigger
        if not trigger_path.exists():
            continue

        required_file = app_path / contract.required_file
        if not required_file.exists():
            errors.append(
                "Platform contract violation: "
                f"{contract.description} is missing required backend file "
                f"{contract.required_file}"
            )
            continue

        content = required_file.read_text(encoding="utf-8")
        missing_markers = [
            marker for marker in contract.markers if marker not in content
        ]
        if missing_markers:
            preview = ", ".join(missing_markers[:4])
            errors.append(
                "Platform contract violation: "
                f"{contract.required_file} is missing required markers: {preview}"
            )

    return errors


def _validate_plan_contract(context: EvolutionContext) -> list[str]:
    """Reject generated output that does not satisfy the planned file contract."""
    if not context.plan:
        return []

    generated_paths = {
        gen_file.file_path.lstrip("/")
        for gen_file in context.generated_files
    }
    expected_paths = {
        change.file_path.lstrip("/")
        for change in context.plan.changes
        if change.file_path not in _AUTO_MANAGED_PLAN_PATHS
    }
    errors: list[str] = []

    missing_paths = sorted(expected_paths - generated_paths)
    if missing_paths:
        preview = ", ".join(missing_paths[:5])
        errors.append(
            "Generated output does not cover all planned files: "
            f"{preview}"
        )

    desktop_shell_paths = sorted(expected_paths & _DESKTOP_SHELL_PATHS)
    if desktop_shell_paths and not _request_allows_desktop_shell_changes(
        context.request.user_request
    ):
        preview = ", ".join(desktop_shell_paths)
        errors.append(
            "Desktop shell files are protected platform infrastructure and may not be "
            f"changed for a product-app request: {preview}. Integrate product apps "
            "through frontend/src/apps/ and AppViewer instead."
        )

    backend_shell_paths = sorted(expected_paths & _BACKEND_SHELL_PATHS)
    if backend_shell_paths:
        preview = ", ".join(backend_shell_paths)
        errors.append(
            "Backend shell files are protected platform infrastructure and may not be "
            f"changed by autonomous product work: {preview}."
        )

    has_migration = any(
        path.startswith("backend/alembic/versions/")
        for path in generated_paths
    )
    if context.plan.requires_migration and not has_migration:
        errors.append(
            "Plan requires a schema migration but no Alembic revision was generated "
            "under backend/alembic/versions/."
        )

    return errors


def _extract_frontend_app_root(relative_path: str) -> str | None:
    """Return the top-level app module directory for frontend/src/apps/* paths."""
    normalized = relative_path.lstrip("/").replace("\\", "/")
    prefix = "frontend/src/apps/"
    if not normalized.startswith(prefix):
        return None

    suffix = normalized[len(prefix):]
    root = suffix.split("/", 1)[0].strip()
    return root or None


def _validate_frontend_app_structure(
    app_path: Path,
    context: EvolutionContext,
) -> list[str]:
    """Reject app-module casing drift and duplicate desktop app roots."""
    errors: list[str] = []
    modules, conflicts = extract_frontend_app_modules(app_path / "frontend")
    module_by_slug: dict[str, str] = {}

    for module in sorted(modules, key=lambda item: item.relative_path):
        module_by_slug.setdefault(module.canonical_key, module.relative_path)

    for conflict in conflicts:
        errors.append(
            "Frontend app module conflict: multiple roots resolve to "
            f"`{conflict.canonical_key}` -> {', '.join(conflict.paths)}. "
            f"Consolidate them into frontend/src/apps/{conflict.canonical_key}/ before "
            "adding more product changes."
        )

    planned_paths = {
        change.file_path.lstrip("/")
        for change in (context.plan.changes if context.plan else [])
    }
    generated_paths = {
        gen_file.file_path.lstrip("/")
        for gen_file in context.generated_files
    }

    for relative_path in sorted(planned_paths | generated_paths):
        module_root = _extract_frontend_app_root(relative_path)
        if module_root is None:
            continue

        canonical_root = canonicalize_frontend_app_key(module_root)
        expected_root = f"frontend/src/apps/{canonical_root}/"
        actual_root = f"frontend/src/apps/{module_root}/"

        if module_root != canonical_root:
            errors.append(
                "Frontend app module roots must use canonical desktop slugs. "
                f"Use `{expected_root}` instead of `{actual_root}`."
            )
            continue

        existing_root = module_by_slug.get(canonical_root)
        if existing_root and existing_root != f"frontend/src/apps/{canonical_root}":
            errors.append(
                "Frontend app module root drift detected: slug "
                f"`{canonical_root}` already maps to `{existing_root}`. Consolidate that "
                "module before creating a sibling root."
            )

    return errors


class DockerSandbox(BaseSandbox):
    """Sandbox that uses Docker to test generated code in isolation."""

    def __init__(self, config: EngineSettings | None = None) -> None:
        self.config = config or settings
        self.client = docker.from_env()
        self.temp_dir: Path | None = None
        self.containers: list[str] = []

    def _resolve_validation_source_path(self) -> Path:
        """Prefer the live evolved app so sandbox validation matches deployment reality."""
        evolved_app_path = Path(self.config.evolved_app_path).resolve()
        if (evolved_app_path / "frontend").exists() and (evolved_app_path / "backend").exists():
            return evolved_app_path
        return Path(self.config.operational_plane_path).resolve()

    def _sandbox_tmp_root(self) -> Path:
        """Use a configurable temp root so production can mount a real tmpfs."""
        preferred = Path(self.config.sandbox_tmp_dir).resolve()
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred

    def _copy_validation_source(self, source: Path, destination: Path) -> None:
        """Copy only the validation-relevant source tree into the sandbox."""
        shutil.copytree(
            source,
            destination,
            ignore=_SANDBOX_COPY_IGNORE,
        )

    def _impacted_services(self, context: EvolutionContext) -> set[str]:
        """Build/test only services touched by this slice when possible."""
        paths = {
            change.file_path.lstrip("/")
            for change in (context.plan.changes if context.plan else [])
        }
        paths.update(gen_file.file_path.lstrip("/") for gen_file in context.generated_files)

        impacted: set[str] = set()
        for path in paths:
            if (
                path.startswith("backend/")
                or path.startswith("app/")
                or path.startswith("migrations/")
            ):
                impacted.add("backend")
            elif path.startswith("frontend/"):
                impacted.add("frontend")
            elif path == "docker-compose.yml" or path.endswith("/Dockerfile"):
                impacted.update({"backend", "frontend"})

        return impacted or {"backend", "frontend"}

    async def run_tests(self, context: EvolutionContext) -> ValidationResult:
        """Execute the three-stage validation pipeline."""
        errors: list[str] = []
        suggestions: list[str] = []
        impacted_services = self._impacted_services(context)

        # Stage 0: Prepare sandbox workspace
        self.temp_dir = Path(
            tempfile.mkdtemp(
                prefix="evo_sandbox_",
                dir=str(self._sandbox_tmp_root()),
            )
        )
        sandbox_app = self.temp_dir / "managed_app"

        # Copy the current managed app into the sandbox
        managed_app_src = self._resolve_validation_source_path()
        if managed_app_src.exists():
            self._copy_validation_source(managed_app_src, sandbox_app)
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

        contract_errors = _validate_plan_contract(context)
        if contract_errors:
            errors.extend(contract_errors)
            suggestions.append(
                "Ensure the generator emits every planned file, including required Alembic migrations."
            )

        structure_errors = _validate_frontend_app_structure(sandbox_app, context)
        if structure_errors:
            errors.extend(structure_errors)
            suggestions.append(
                "Use the canonical desktop app module roots from frontend/src/apps/ and "
                "avoid casing or slug variants that create sibling app trees."
            )

        platform_errors = _validate_platform_contract_files(
            sandbox_app,
            context.request.user_request,
            self.config.runtime_contracts_path,
        )
        if platform_errors:
            errors.extend(platform_errors)
            suggestions.append(
                "Preserve framework-owned platform capabilities such as the desktop shell, chat, "
                "cost view, and stable backend contracts for mounted apps."
            )

        # Stage 1: Static analysis
        static_ok, static_errors = await self._run_static_analysis(sandbox_app)
        if not static_ok:
            errors.extend(static_errors)
            suggestions.append("Fix linting errors before proceeding")

        # Stage 2: Build test
        build_ok, build_errors = await self._run_build_test(
            sandbox_app,
            impacted_services=impacted_services,
        )
        if not build_ok:
            errors.extend(build_errors)
            suggestions.append("Ensure Dockerfiles build without errors")

        # Stage 2.25: Alembic head check for schema-changing plans
        alembic_ok = True
        if build_ok and "backend" in impacted_services:
            alembic_ok, alembic_errors = await self._run_alembic_head_check(context)
            if not alembic_ok:
                errors.extend(alembic_errors)
                suggestions.append(
                    "Alembic migrations must form a single linear head and extend the current revision chain."
                )

        # Stage 2.5: Backend startup smoke test
        smoke_ok = "backend" not in impacted_services
        if build_ok and alembic_ok and "backend" in impacted_services:
            smoke_ok, smoke_errors = await self._run_backend_import_smoke_test()
            if not smoke_ok:
                errors.extend(smoke_errors)
                suggestions.append("Backend changes must import app.main successfully before deploy")

        # Stage 3: Integration test (only if build passed)
        tests_ok = "backend" not in impacted_services
        if build_ok and smoke_ok and "backend" in impacted_services:
            tests_ok, test_errors = await self._run_integration_tests(sandbox_app)
            if not tests_ok:
                errors.extend(test_errors)
                suggestions.append("Fix failing tests and ensure endpoints return HTTP 200")

        # Calculate risk score (0.0 = safe, 1.0 = dangerous)
        risk_score = 0.0
        if not static_ok:
            risk_score += 0.2
        if structure_errors:
            risk_score += 0.3
        if platform_errors:
            risk_score += 0.4
        if not build_ok:
            risk_score += 0.5
        elif not alembic_ok:
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
            passed=static_ok and build_ok and alembic_ok and smoke_ok,
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

    async def _run_build_test(
        self,
        app_path: Path,
        *,
        impacted_services: set[str],
    ) -> tuple[bool, list[str]]:
        """Attempt to docker build each service.

        For the backend, tries the ``test`` stage first (for pytest). If that
        fails, falls back to ``base`` stage — the code still compiles and the
        evolution should not be blocked just because dev deps fail to install.
        """
        errors: list[str] = []

        for service in ["backend", "frontend"]:
            if service not in impacted_services:
                logger.info("build.skipped_unimpacted_service", service=service)
                continue
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

    async def _run_alembic_head_check(self, context: EvolutionContext) -> tuple[bool, list[str]]:
        """Reject schema changes that create multiple Alembic heads."""
        if not context.plan or not context.plan.requires_migration:
            return True, []

        errors: list[str] = []

        try:
            container = self.client.containers.run(
                "evo-sandbox-backend:test",
                command=["sh", "-lc", "cd /app && export PYTHONPATH=/app && alembic heads"],
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
                errors.append(f"Alembic head check failed:\n{logs[-2000:]}")
                return False, errors

            head_lines = [line for line in logs.splitlines() if "(head)" in line]
            if len(head_lines) != 1:
                errors.append(
                    "Alembic revisions must have exactly one head before deploy.\n"
                    f"Detected heads:\n{logs[-2000:]}"
                )
            else:
                logger.info("tests.alembic.single_head_passed", head=head_lines[0])

        except docker.errors.ContainerError as exc:
            errors.append(f"Alembic head check container error: {exc}")
        except docker.errors.ImageNotFound:
            errors.append("Alembic head check image not found")
        except Exception as exc:
            errors.append(f"Alembic head check error: {exc}")

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
