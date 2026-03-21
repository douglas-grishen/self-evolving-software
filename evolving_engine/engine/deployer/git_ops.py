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

logger = structlog.get_logger()

_RESTART_HEALTH_TIMEOUT_SECONDS = 45.0
_RESTART_HEALTH_POLL_SECONDS = 2.0
_RESTART_HEALTH_PATHS = ("/health", "/api/v1/health", "/api/v1/system/info")


class LocalDeployer:
    """Deploys validated code to the local evolved-app directory.

    Lifecycle:
    - ``deploy(ctx)`` — copy files, git commit locally, rebuild Docker
    - ``rollback()`` — revert last commit, rebuild Docker
    """

    def __init__(self, config: EngineSettings | None = None) -> None:
        self.config = config or settings

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
        """Ensure path is a git repo. Bootstrap from managed_app template if needed."""
        try:
            return Repo(path)
        except (InvalidGitRepositoryError, Exception):
            pass

        # Bootstrap: copy managed_app template and init git
        managed_app_src = Path(self.config.managed_app_path).resolve()
        if not managed_app_src.exists():
            logger.error("deploy.bootstrap_failed", reason="managed_app_path not found")
            return None

        try:
            logger.info("deploy.bootstrap", source=str(managed_app_src), target=str(path))
            path.mkdir(parents=True, exist_ok=True)

            # Copy template (backend/ and frontend/)
            for item in managed_app_src.iterdir():
                dst = path / item.name
                if item.is_dir():
                    shutil.copytree(item, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dst)

            # Init git repo
            repo = Repo.init(path)
            repo.git.add("-A")
            repo.index.commit("initial: base template from managed_app")
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
            repo.git.revert("HEAD", "--no-edit")
            logger.info("deploy.rollback_success")
            return True
        except Exception as exc:
            logger.error("deploy.rollback_error", error=str(exc))
            return False

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
                m = re.search(r"DEPLOY_VERSION\s*=\s*(\d+)", content)
                if m:
                    current = int(m.group(1))
            except Exception:
                pass

        new_version = current + 1
        version_file.parent.mkdir(parents=True, exist_ok=True)
        version_file.write_text(
            '"""Auto-generated deploy version — updated by the Self-Evolving Engine on each deploy.\n\n'
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

            logger.info("deploy.restart_success", health_url=self.config.monitor_url)
            return True, "Services rebuilt and restarted"

        except asyncio.TimeoutError:
            return False, "Docker rebuild/restart timed out"
        except Exception as exc:
            return False, str(exc)

    async def _wait_for_backend_health(self) -> tuple[bool, str]:
        """Wait for the restarted backend to serve a healthy HTTP response."""
        base_url = self.config.monitor_url.rstrip("/")
        deadline = asyncio.get_running_loop().time() + _RESTART_HEALTH_TIMEOUT_SECONDS
        last_error = "no response yet"

        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
            while asyncio.get_running_loop().time() < deadline:
                for path in _RESTART_HEALTH_PATHS:
                    url = f"{base_url}{path}"
                    try:
                        resp = await client.get(url)
                        if resp.status_code < 500:
                            logger.info(
                                "deploy.health_check_ok",
                                url=url,
                                status_code=resp.status_code,
                            )
                            return True, f"{url} -> {resp.status_code}"
                        last_error = f"{url} -> HTTP {resp.status_code}"
                    except Exception as exc:
                        last_error = f"{url} -> {exc}"

                await asyncio.sleep(_RESTART_HEALTH_POLL_SECONDS)

        logger.warning("deploy.health_check_failed", error=last_error)
        return False, last_error

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
