"""Repository scanner — walks the managed_app/ filesystem and extracts structure."""

import json
import re
from pathlib import Path

import structlog

from engine.models.repo_map import (
    APIEndpoint,
    DBColumn,
    DBSchema,
    DBTable,
    Dependency,
    FileNode,
    FrontendAppModule,
    RepoMap,
    RepoPathConflict,
    StaticAsset,
)

logger = structlog.get_logger()

# Directories and patterns to skip during scanning
IGNORE_DIRS = {
    "__pycache__",
    "node_modules",
    ".git",
    ".venv",
    "venv",
    ".next",
    "dist",
    "build",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "alembic/versions",
}

IGNORE_EXTENSIONS = {".pyc", ".pyo", ".egg-info", ".whl"}

_NOTABLE_ASSET_LIMIT = 6
_NOTABLE_ASSET_MIN_BYTES = 256 * 1024


def scan_directory(root: Path, relative_to: Path | None = None) -> FileNode:
    """Recursively scan a directory and build a FileNode tree."""
    rel = relative_to or root
    node = FileNode(
        path=str(root.relative_to(rel)),
        name=root.name,
        is_dir=True,
    )

    try:
        for child in sorted(root.iterdir()):
            if child.name.startswith(".") and child.is_dir():
                continue
            if child.name in IGNORE_DIRS:
                continue

            if child.is_dir():
                node.children.append(scan_directory(child, rel))
            elif child.suffix not in IGNORE_EXTENSIONS:
                node.children.append(
                    FileNode(
                        path=str(child.relative_to(rel)),
                        name=child.name,
                        is_dir=False,
                        extension=child.suffix,
                        size_bytes=child.stat().st_size,
                    )
                )
    except PermissionError:
        logger.warning("scan.permission_denied", path=str(root))

    return node


def extract_fastapi_endpoints(backend_path: Path) -> list[APIEndpoint]:
    """Extract FastAPI route definitions by scanning Python source files."""
    endpoints: list[APIEndpoint] = []
    api_dir = backend_path / "app" / "api"

    if not api_dir.exists():
        return endpoints

    pattern = re.compile(
        r'@\w+\.(get|post|put|delete|patch)\(\s*"([^"]+)"',
        re.IGNORECASE,
    )

    for py_file in api_dir.rglob("*.py"):
        try:
            content = py_file.read_text()
            for match in pattern.finditer(content):
                method = match.group(1).upper()
                path = match.group(2)
                endpoints.append(
                    APIEndpoint(
                        method=method,
                        path=path,
                        file_path=str(py_file.relative_to(backend_path)),
                    )
                )
        except Exception as exc:
            logger.warning("scan.endpoint_error", file=str(py_file), error=str(exc))

    return endpoints


def extract_react_components(frontend_path: Path) -> list[str]:
    """Extract React component names from TSX/JSX files."""
    components: list[str] = []
    src_dir = frontend_path / "src"

    if not src_dir.exists():
        return components

    pattern = re.compile(r"export\s+(?:default\s+)?function\s+(\w+)")

    for tsx_file in src_dir.rglob("*.tsx"):
        try:
            content = tsx_file.read_text()
            for match in pattern.finditer(content):
                components.append(f"{match.group(1)} ({tsx_file.relative_to(frontend_path)})")
        except Exception as exc:
            logger.warning("scan.component_error", file=str(tsx_file), error=str(exc))

    return components


def canonicalize_frontend_app_key(name: str) -> str:
    """Convert a frontend app directory name into the stable desktop slug."""
    with_word_boundaries = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", name.strip())
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", with_word_boundaries.lower()))


def extract_frontend_app_modules(
    frontend_path: Path,
) -> tuple[list[FrontendAppModule], list[RepoPathConflict]]:
    """List mounted desktop app modules and detect casing/path conflicts."""
    modules: list[FrontendAppModule] = []
    conflicts: list[RepoPathConflict] = []
    apps_dir = frontend_path / "src" / "apps"

    if not apps_dir.exists():
        return modules, conflicts

    grouped_paths: dict[str, list[str]] = {}
    for child in sorted(apps_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not any(descendant.is_file() for descendant in child.rglob("*")):
            continue

        relative_path = f"frontend/{child.relative_to(frontend_path).as_posix()}"
        canonical_key = canonicalize_frontend_app_key(child.name)
        has_entrypoint = (child / "index.ts").exists() or (child / "index.tsx").exists()
        modules.append(
            FrontendAppModule(
                module_key=child.name,
                relative_path=relative_path,
                canonical_key=canonical_key,
                has_entrypoint=has_entrypoint,
            )
        )
        grouped_paths.setdefault(canonical_key, []).append(relative_path)

    for canonical_key, paths in sorted(grouped_paths.items()):
        unique_paths = sorted(set(paths))
        if len(unique_paths) < 2:
            continue
        conflicts.append(
            RepoPathConflict(
                canonical_key=canonical_key,
                paths=unique_paths,
                description=(
                    "Multiple frontend app roots resolve to the same desktop slug. "
                    f"Use only frontend/src/apps/{canonical_key}/."
                ),
            )
        )

    return modules, conflicts


def extract_public_assets(managed_app_path: Path) -> list[StaticAsset]:
    """Expose notable static assets so planners know they are part of the live UI."""
    assets: list[StaticAsset] = []
    public_dir = managed_app_path / "frontend" / "public"

    if not public_dir.exists():
        return assets

    for asset_path in sorted(public_dir.rglob("*")):
        if not asset_path.is_file():
            continue
        size_bytes = asset_path.stat().st_size
        if size_bytes < _NOTABLE_ASSET_MIN_BYTES:
            continue
        assets.append(
            StaticAsset(
                relative_path=f"frontend/{asset_path.relative_to(managed_app_path / 'frontend').as_posix()}",
                size_bytes=size_bytes,
            )
        )

    assets.sort(key=lambda asset: asset.size_bytes, reverse=True)
    return assets[:_NOTABLE_ASSET_LIMIT]


def extract_dependencies(managed_app_path: Path) -> list[Dependency]:
    """Extract dependencies from pyproject.toml and package.json."""
    deps: list[Dependency] = []

    # Python backend dependencies
    pyproject = managed_app_path / "backend" / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text()
        in_deps = False
        for line in content.splitlines():
            if line.strip().startswith("dependencies"):
                in_deps = True
                continue
            if in_deps and line.strip().startswith("]"):
                in_deps = False
                continue
            if in_deps and '"' in line:
                dep = line.strip().strip('",')
                name = re.split(r"[><=!~]", dep)[0].strip()
                version = dep[len(name):].strip()
                deps.append(Dependency(name=name, version=version, layer="backend"))

    # Frontend dependencies
    pkg_json = managed_app_path / "frontend" / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text())
            for name, version in data.get("dependencies", {}).items():
                deps.append(Dependency(name=name, version=version, layer="frontend"))
        except json.JSONDecodeError:
            logger.warning("scan.package_json_error")

    return deps


def extract_alembic_revisions(managed_app_path: Path) -> list[str]:
    """Extract Alembic revision lineage from migration files."""
    revisions: list[str] = []
    versions_dir = managed_app_path / "backend" / "alembic" / "versions"

    if not versions_dir.exists():
        return revisions

    revision_pattern = re.compile(r'^revision\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
    down_revision_pattern = re.compile(
        r'^down_revision\s*=\s*(?:["\']([^"\']+)["\']|None)',
        re.MULTILINE,
    )

    for migration_file in sorted(versions_dir.glob("*.py")):
        try:
            content = migration_file.read_text()
            revision_match = revision_pattern.search(content)
            if not revision_match:
                continue
            down_revision_match = down_revision_pattern.search(content)
            down_revision = (
                down_revision_match.group(1)
                if down_revision_match and down_revision_match.group(1)
                else "None"
            )
            revisions.append(
                f"{revision_match.group(1)} -> {down_revision} ({migration_file.name})"
            )
        except Exception as exc:
            logger.warning("scan.alembic_revision_error", file=str(migration_file), error=str(exc))

    return revisions


def build_repo_map(managed_app_path: Path) -> RepoMap:
    """Build a complete RepoMap from the managed application directory."""
    logger.info("scan.start", path=str(managed_app_path))

    tree = scan_directory(managed_app_path, managed_app_path)
    endpoints = extract_fastapi_endpoints(managed_app_path / "backend")
    components = extract_react_components(managed_app_path / "frontend")
    dependencies = extract_dependencies(managed_app_path)
    alembic_revisions = extract_alembic_revisions(managed_app_path)
    frontend_app_modules, path_conflicts = extract_frontend_app_modules(
        managed_app_path / "frontend"
    )
    public_assets = extract_public_assets(managed_app_path)

    repo_map = RepoMap(
        tree=tree,
        api_endpoints=endpoints,
        frontend_app_modules=frontend_app_modules,
        path_conflicts=path_conflicts,
        public_assets=public_assets,
        react_components=components,
        dependencies=dependencies,
        alembic_revisions=alembic_revisions,
        summary=(
            f"Managed app with {len(endpoints)} API endpoints, "
            f"{len(frontend_app_modules)} frontend app modules, "
            f"{len(path_conflicts)} structural path conflicts, "
            f"{len(components)} React components, "
            f"{len(dependencies)} dependencies, "
            f"and {len(alembic_revisions)} Alembic revisions."
        ),
    )

    logger.info(
        "scan.complete",
        endpoints=len(endpoints),
        frontend_app_modules=len(frontend_app_modules),
        path_conflicts=len(path_conflicts),
        components=len(components),
        dependencies=len(dependencies),
    )

    return repo_map
