"""Local Deployer — applies validated code to the local evolved-app repo.

Architecture: two-layer separation for open-source compatibility.

  /opt/self-evolving-software/   (Framework — from GitHub, read-only)
  /opt/evolved-app/              (Instance code — local git, NEVER pushed)

The engine generates and validates code in a sandbox, then this deployer:
1. Copies validated files to /opt/evolved-app/
2. Commits to the LOCAL git repo (for history + rollback)
3. Rebuilds Docker images via docker compose
4. Restarts affected services

The open-source repo on GitHub stays clean — only framework/engine code lives there.
Instance-specific evolved code lives exclusively on the EC2 instance.
"""

import asyncio
import re
import shutil
from pathlib import Path

import httpx
import structlog
from git import Repo
from git.exc import InvalidGitRepositoryError

from engine.config import EngineSettings, settings
from engine.context import EvolutionContext
from engine.models.evolution import DeploymentResult
from engine.runtime_contracts import (
    get_core_availability_probes,
    get_core_framework_probes,
    get_runtime_contract_probes,
    validate_runtime_contract_response,
)

logger = structlog.get_logger()

_RESTART_HEALTH_TIMEOUT_SECONDS = 45.0
_RESTART_HEALTH_POLL_SECONDS = 2.0
_RUNTIME_ARTIFACT_PATHS = (
    ".engine-state/usage.json",
    ".instance-state/usage.json",
)
_RUNTIME_ARTIFACT_SUFFIXES = (".pyc", ".pyo")
_RUNTIME_ARTIFACT_SEGMENTS = ("__pycache__",)
_DEPLOY_VERSION_RE = re.compile(r"DEPLOY_VERSION(?:\s*:\s*[^=]+)?\s*=\s*(\d+)")
_PROTECTED_FRAMEWORK_FILES_PATH = Path(__file__).resolve().parents[3] / "protected_framework_files.txt"

class LocalDeployer:
    """Deploys validated code to the local evolved-app directory.

    Lifecycle:
    - ``deploy(ctx)`` — copy files, git commit locally, rebuild Docker
    - ``rollback()`` — revert last commit, rebuild Docker
    """

    def __init__(self, config: EngineSettings | None = None) -> None:
        self.config = config or settings

    def _framework_template_root(self) -> Path:
        """Return the framework-managed app template root."""
        repo_managed_app = Path(self.config.repo_root).resolve() / "managed_app"
        if repo_managed_app.exists():
            return repo_managed_app
        return Path(self.config.managed_app_path).resolve()

    def _sync_framework_core_files(self, evolved_path: Path) -> int:
        """Restore framework-owned core backend files into the evolved app."""
        if not _PROTECTED_FRAMEWORK_FILES_PATH.exists():
            return 0

        template_root = self._framework_template_root()
        copied = 0
        for raw_line in _PROTECTED_FRAMEWORK_FILES_PATH.read_text(encoding="utf-8").splitlines():
            rel_path = raw_line.strip()
            if not rel_path or rel_path.startswith("#"):
                continue
            src = template_root / rel_path
            dst = evolved_path / rel_path
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1

        if copied:
            logger.info("deploy.framework_core_synced", files=copied)
        return copied

    async def deploy(self, context: EvolutionContext) -> DeploymentResult:
        """Apply generated files to evolved-app, commit locally, and rebuild.

        Steps:
        1. Ensure evolved-app repo exists (bootstrap if first run)
        2. Copy validated files from workspace to evolved-app/
        3. Git add + commit (local only — never push)
        4. Rebuild and restart Docker services
        """
        evolved_path = Path(self.config.evolved_app_path).resolve()
        workspace = Path(self.config.workspace_path) / context.request_id

        if not workspace.exists():
            return DeploymentResult(
                success=False,
                message=f"Workspace not found: {workspace}",
            )

        # Ensure evolved-app exists and is a git repo
        repo = self._ensure_repo(evolved_path)
        if repo is None:
            return DeploymentResult(
                success=False,
                message=f"Could not initialize evolved-app repo at {evolved_path}",
            )

        # Copy generated files to evolved-app/
        files_copied = 0
        for gen_file in context.generated_files:
            src = workspace / gen_file.file_path
            dst = evolved_path / gen_file.file_path

            if gen_file.action == "delete":
                dst.unlink(missing_ok=True)
                logger.debug("deploy.file_deleted", path=gen_file.file_path)
                files_copied += 1
            elif src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                logger.debug("deploy.file_copied", path=gen_file.file_path)
                files_copied += 1

        # Runtime artifacts inside evolved-app are not source changes and must not
        # participate in commits or block later rollback attempts.
        self._sync_framework_core_files(evolved_path)
        self._restore_runtime_artifacts(repo)

        # Increment the deploy version baked into the image.
        # This counter is exposed by GET /api/v1/system/info and shown on the desktop.
        new_version = self._increment_deploy_version(evolved_path)
        logger.info("deploy.version_bumped", version=new_version)

        # Git commit (local only)
        commit_sha = self._commit(repo, context)

        # Rebuild and restart Docker services
        rebuild_ok, rebuild_msg = await self._rebuild_services()

        if not rebuild_ok:
            # Rollback the commit if rebuild fails
            logger.warning("deploy.rebuild_failed — rolling back", error=rebuild_msg)
            self._rollback(repo)
            await self._rebuild_services()  # Rebuild with previous code
            return DeploymentResult(
                success=False,
                commit_sha=commit_sha,
                message=f"Rebuild failed (rolled back): {rebuild_msg}",
            )

        logger.info(
            "deploy.success",
            commit_sha=commit_sha[:8] if commit_sha else "none",
            files=files_copied,
        )

        return DeploymentResult(
            success=True,
            commit_sha=commit_sha,
            message=f"Deployed {files_copied} files locally (commit {commit_sha[:8]})",
        )

    def _ensure_repo(self, path: Path) -> Repo | None:
        """Ensure path is a git repo. Bootstrap from the Operational Plane template if needed."""
        try:
            return Repo(path)
        except (InvalidGitRepositoryError, Exception):
            pass

        # Bootstrap: copy managed_app template and init git
        operational_plane_src = Path(self.config.operational_plane_path).resolve()
        if not operational_plane_src.exists():
            logger.error(
                "deploy.bootstrap_failed", reason="operational_plane_path not found"
            )
            return None

        try:
            logger.info(
                "deploy.bootstrap",
                source=str(operational_plane_src),
                target=str(path),
            )
            path.mkdir(parents=True, exist_ok=True)

            # Copy template (backend/ and frontend/)
            for item in operational_plane_src.iterdir():
                dst = path / item.name
                if item.is_dir():
                    shutil.copytree(item, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dst)

            # Init git repo
            repo = Repo.init(path)
            repo.git.add("-A")
            repo.index.commit("initial: base template from operational plane")
            logger.info("deploy.bootstrap_complete")
            return repo

        except Exception as exc:
            logger.error("deploy.bootstrap_error", error=str(exc))
            return None

    def _commit(self, repo: Repo, context: EvolutionContext) -> str:
        """Create a local git commit with evolution metadata."""
        try:
            repo.git.add("-A")

            # Check if there are actual changes to commit
            if not repo.is_dirty() and not repo.untracked_files:
                logger.info("deploy.no_changes")
                return ""

            summary = context.plan.summary if context.plan else "Autonomous evolution"
            risk = context.validation_result.risk_score if context.validation_result else "N/A"

            message = (
                f"evo: {summary}\n\n"
                f"Request ID: {context.request_id}\n"
                f"Source: {context.request.source.value}\n"
                f"Files changed: {len(context.generated_files)}\n"
                f"Risk score: {risk}\n\n"
                f"Generated by Self-Evolving Software Engine"
            )

            commit = repo.index.commit(message)
            logger.info("deploy.committed", sha=commit.hexsha[:8])
            return commit.hexsha

        except Exception as exc:
            logger.error("deploy.commit_error", error=str(exc))
            return ""

    def _rollback(self, repo: Repo) -> bool:
        """Revert the last commit (for failed rebuilds)."""
        try:
            self._restore_runtime_artifacts(repo)
            repo.git.revert("HEAD", "--no-edit")
            logger.info("deploy.rollback_success")
            return True
        except Exception as exc:
            logger.error("deploy.rollback_error", error=str(exc))
            return False

    def _restore_runtime_artifacts(self, repo: Repo) -> None:
        """Remove generated runtime artifacts so commits/rollbacks stay deterministic."""
        repo_root = Path(repo.working_tree_dir or ".").resolve()

        dirty_paths: set[str] = set()
        dirty_paths.update(diff.a_path for diff in repo.index.diff(None) if diff.a_path)
        dirty_paths.update(diff.b_path for diff in repo.index.diff(None) if diff.b_path)

        for rel_path in sorted(dirty_paths):
            if not self._is_runtime_artifact(Path(rel_path)):
                continue
            try:
                repo.git.restore("--source=HEAD", "--staged", "--worktree", rel_path)
            except Exception:
                try:
                    repo.git.checkout("HEAD", "--", rel_path)
                except Exception as exc:
                    logger.debug(
                        "deploy.runtime_artifact_restore_failed",
                        path=rel_path,
                        error=str(exc),
                    )

        for rel_path in list(repo.untracked_files):
            artifact_path = Path(rel_path)
            if not self._is_runtime_artifact(artifact_path):
                continue
            full_path = repo_root / artifact_path
            try:
                if full_path.is_dir():
                    shutil.rmtree(full_path, ignore_errors=True)
                else:
                    full_path.unlink(missing_ok=True)
            except Exception as exc:
                logger.debug(
                    "deploy.runtime_artifact_delete_failed",
                    path=rel_path,
                    error=str(exc),
                )

    def _is_runtime_artifact(self, path: Path) -> bool:
        """Return whether a repo-relative path is generated at runtime."""
        if path.as_posix() in _RUNTIME_ARTIFACT_PATHS:
            return True
        if path.suffix in _RUNTIME_ARTIFACT_SUFFIXES:
            return True
        return any(segment in path.parts for segment in _RUNTIME_ARTIFACT_SEGMENTS)

    def _increment_deploy_version(self, evolved_path: Path) -> int:
        """Bump the deploy version counter baked into backend/app/_deploy_version.py.

        The file is written before the git commit so the new version number
        gets baked into the Docker image on rebuild.  The frontend reads it
        via GET /api/v1/system/info and shows it as a subtle badge.
        """
        version_file = evolved_path / "backend" / "app" / "_deploy_version.py"
        current = 0

        if version_file.exists():
            try:
                content = version_file.read_text()
                m = _DEPLOY_VERSION_RE.search(content)
                if m:
                    current = int(m.group(1))
            except Exception:
                pass

        new_version = current + 1
        version_file.parent.mkdir(parents=True, exist_ok=True)
        version_file.write_text(
            '"""Auto-generated deploy version — updated by the '
            'Self-Evolving Engine on each deploy.\n\n'
            "Do NOT edit manually. The engine increments this counter after every successful\n"
            "code deployment so the UI can display how many autonomous evolutions have run.\n"
            '"""\n\n'
            f"DEPLOY_VERSION: int = {new_version}\n"
        )
        return new_version

    async def _rebuild_services(self) -> tuple[bool, str]:
        """Rebuild and restart managed system Docker services.

        Only rebuilds backend and frontend — the engine and postgres are
        managed by the framework compose file separately.

        Uses ``-p <project>`` to match the existing Docker Compose stack,
        preventing creation of duplicate containers when the engine runs
        from a different working directory (e.g. /workspace inside the
        container vs /opt/self-evolving-software on the host).
        """
        deploy_root = Path(self.config.deploy_root).resolve()
        compose_file = self.config.compose_file
        project = self.config.compose_project

        if not (deploy_root / compose_file).exists():
            return False, f"Compose file not found: {deploy_root / compose_file}"

        # Base compose command with project name to match existing stack
        compose_cmd = [
            "docker", "compose",
            "-p", project,
            "-f", str(deploy_root / compose_file),
        ]

        try:
            # Rebuild backend + frontend images
            proc = await asyncio.create_subprocess_exec(
                *compose_cmd, "build", "backend", "frontend",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(deploy_root),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

            if proc.returncode != 0:
                return False, f"Build failed: {stderr.decode()[-1000:]}"

            logger.info("deploy.build_success")

            # Restart only backend + frontend (not engine, not postgres)
            proc = await asyncio.create_subprocess_exec(
                *compose_cmd, "up", "-d", "--no-deps", "backend", "frontend",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(deploy_root),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode != 0:
                return False, f"Restart failed: {stderr.decode()[-1000:]}"

            backend_ready, backend_msg = await self._wait_for_backend_health()
            if not backend_ready:
                backend_logs = await self._collect_backend_logs(compose_cmd, deploy_root)
                return False, (
                    "Backend did not become healthy after restart. "
                    f"{backend_msg}\n{backend_logs}"
                )

            contracts_ok, contracts_msg = await self._run_runtime_contract_smoke_checks()
            if not contracts_ok:
                backend_logs = await self._collect_backend_logs(compose_cmd, deploy_root)
                return False, (
                    "Runtime contract smoke checks failed after restart. "
                    f"{contracts_msg}\n{backend_logs}"
                )

            logger.info("deploy.restart_success", health_url=self.config.monitor_url)
            return True, "Services rebuilt and restarted"

        except TimeoutError:
            return False, "Docker rebuild/restart timed out"
        except Exception as exc:
            return False, str(exc)

    async def _wait_for_backend_health(self) -> tuple[bool, str]:
        """Wait for the restarted backend to serve a healthy HTTP response."""
        base_url = self.config.monitor_url.rstrip("/")
        deadline = asyncio.get_running_loop().time() + _RESTART_HEALTH_TIMEOUT_SECONDS
        last_error = "no response yet"
        probes = get_core_availability_probes()

        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
            while asyncio.get_running_loop().time() < deadline:
                for probe in probes:
                    url = f"{base_url}{probe.path}"
                    try:
                        resp = await client.request(probe.method, url, json=probe.json_body)
                        contract_error = validate_runtime_contract_response(probe, resp)
                        if contract_error is None:
                            logger.info(
                                "deploy.health_check_ok",
                                url=url,
                                status_code=resp.status_code,
                            )
                            return True, f"{url} -> {resp.status_code}"
                        last_error = f"{url} -> {contract_error}"
                    except Exception as exc:
                        last_error = f"{url} -> {exc}"

                await asyncio.sleep(_RESTART_HEALTH_POLL_SECONDS)

        logger.warning("deploy.health_check_failed", error=last_error)
        return False, last_error

    async def _run_runtime_contract_smoke_checks(self) -> tuple[bool, str]:
        """Verify mounted app contracts before accepting a restarted deployment."""
        probes = list(get_core_framework_probes())
        probes.extend(
            get_runtime_contract_probes(
                Path(self.config.evolved_app_path),
                self.config.runtime_contracts_path,
            )
        )
        if not probes:
            return True, "No mounted app contract probes configured"

        base_url = self.config.monitor_url.rstrip("/")
        timeout = httpx.Timeout(5.0, connect=2.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            for probe in probes:
                url = f"{base_url}{probe.path}"
                try:
                    response = await client.request(
                        probe.method,
                        url,
                        json=probe.json_body,
                    )
                except Exception as exc:
                    logger.warning(
                        "deploy.contract_smoke_failed",
                        method=probe.method,
                        path=probe.path,
                        error=str(exc),
                    )
                    return False, f"{probe.method} {probe.path} -> {exc}"

                contract_error = validate_runtime_contract_response(probe, response)
                if contract_error is not None:
                    logger.warning(
                        "deploy.contract_smoke_failed",
                        method=probe.method,
                        path=probe.path,
                        status_code=response.status_code,
                        error=contract_error,
                    )
                    return (
                        False,
                        f"{probe.method} {probe.path} -> {contract_error}",
                    )

                logger.info(
                    "deploy.contract_smoke_ok",
                    method=probe.method,
                    path=probe.path,
                    status_code=response.status_code,
                )

        return True, f"Validated {len(probes)} runtime contract probes"

    async def _collect_backend_logs(self, compose_cmd: list[str], deploy_root: Path) -> str:
        """Fetch recent backend logs to explain a failed restart."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *compose_cmd, "logs", "--tail", "80", "backend",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(deploy_root),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode().strip() or stderr.decode().strip()
            if output:
                return f"Recent backend logs:\n{output[-2000:]}"
        except Exception as exc:
            logger.warning("deploy.collect_backend_logs_failed", error=str(exc))

        return "Recent backend logs unavailable"
