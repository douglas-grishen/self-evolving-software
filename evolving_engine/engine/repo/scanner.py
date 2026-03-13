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
    RepoMap,
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


def build_repo_map(managed_app_path: Path) -> RepoMap:
    """Build a complete RepoMap from the managed application directory."""
    logger.info("scan.start", path=str(managed_app_path))

    tree = scan_directory(managed_app_path, managed_app_path)
    endpoints = extract_fastapi_endpoints(managed_app_path / "backend")
    components = extract_react_components(managed_app_path / "frontend")
    dependencies = extract_dependencies(managed_app_path)

    repo_map = RepoMap(
        tree=tree,
        api_endpoints=endpoints,
        react_components=components,
        dependencies=dependencies,
        summary=(
            f"Managed app with {len(endpoints)} API endpoints, "
            f"{len(components)} React components, "
            f"{len(dependencies)} dependencies."
        ),
    )

    logger.info(
        "scan.complete",
        endpoints=len(endpoints),
        components=len(components),
        dependencies=len(dependencies),
    )

    return repo_map
