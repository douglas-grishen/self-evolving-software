"""Tests for sandbox plan contract validation."""

from pathlib import Path

from engine.config import EngineSettings
from engine.context import create_context
from engine.models.evolution import EvolutionPlan, FileChange, GeneratedFile
from engine.sandbox.docker_sandbox import (
    DockerSandbox,
    _validate_frontend_app_structure,
    _validate_plan_contract,
    _validate_platform_contract_files,
)


def test_contract_requires_generated_migration_when_plan_demands_it():
    """Schema-changing plans must emit an Alembic revision file."""
    ctx = create_context("Add company discovery tables")
    ctx = ctx.model_copy(
        update={
            "plan": EvolutionPlan(
                summary="Add company discovery tables",
                changes=[
                    FileChange(
                        file_path="backend/app/models/company.py",
                        action="create",
                        description="Add company models",
                        layer="database",
                    ),
                    FileChange(
                        file_path="backend/alembic/versions/006_add_company_tables.py",
                        action="create",
                        description="Add company tables migration",
                        layer="database",
                    ),
                ],
                requires_migration=True,
                risk_level="medium",
                reasoning="Adds persisted tables",
            ),
            "generated_files": [
                GeneratedFile(
                    file_path="backend/app/models/company.py",
                    content="class Company: ...",
                    action="create",
                    layer="database",
                )
            ],
        }
    )

    errors = _validate_plan_contract(ctx)

    assert any("schema migration" in error for error in errors)
    assert any("backend/alembic/versions/006_add_company_tables.py" in error for error in errors)


def test_contract_ignores_auto_managed_plan_paths():
    """Framework-managed files should not be required in generator output."""
    ctx = create_context("Add companies API")
    ctx = ctx.model_copy(
        update={
            "plan": EvolutionPlan(
                summary="Add companies API",
                changes=[
                    FileChange(
                        file_path="backend/app/models/__init__.py",
                        action="modify",
                        description="Framework-managed path should be ignored",
                        layer="database",
                    ),
                    FileChange(
                        file_path="backend/app/api/v1/companies.py",
                        action="create",
                        description="Add companies router",
                        layer="backend",
                    ),
                ],
                requires_migration=False,
                risk_level="low",
                reasoning="Adds a new API module",
            ),
            "generated_files": [
                GeneratedFile(
                    file_path="backend/app/api/v1/companies.py",
                    content="router = object()",
                    action="create",
                    layer="backend",
                )
            ],
        }
    )

    errors = _validate_plan_contract(ctx)

    assert errors == []


def test_contract_rejects_desktop_shell_overwrite_for_product_app_request():
    """Product app work must not replace the desktop shell entrypoint."""
    ctx = create_context("Build a company discovery app")
    ctx = ctx.model_copy(
        update={
            "plan": EvolutionPlan(
                summary="Replace root app with company discovery",
                changes=[
                    FileChange(
                        file_path="frontend/src/App.tsx",
                        action="modify",
                        description="Replace desktop with product app",
                        layer="frontend",
                    ),
                ],
                requires_migration=False,
                risk_level="high",
                reasoning="Would repurpose the root shell",
            ),
            "generated_files": [
                GeneratedFile(
                    file_path="frontend/src/App.tsx",
                    content="export default function App() { return null; }",
                    action="modify",
                    layer="frontend",
                )
            ],
        }
    )

    errors = _validate_plan_contract(ctx)

    assert any("Desktop shell files are protected" in error for error in errors)


def test_contract_allows_desktop_shell_change_when_request_is_explicit():
    """Explicit desktop shell requests are allowed through the contract."""
    ctx = create_context("Redesign the desktop shell and menu bar")
    ctx = ctx.model_copy(
        update={
            "plan": EvolutionPlan(
                summary="Refresh desktop shell",
                changes=[
                    FileChange(
                        file_path="frontend/src/App.tsx",
                        action="modify",
                        description="Update desktop shell layout",
                        layer="frontend",
                    ),
                ],
                requires_migration=False,
                risk_level="medium",
                reasoning="This request explicitly targets the shell",
            ),
            "generated_files": [
                GeneratedFile(
                    file_path="frontend/src/App.tsx",
                    content="export default function App() { return null; }",
                    action="modify",
                    layer="frontend",
                )
            ],
        }
    )

    errors = _validate_plan_contract(ctx)

    assert errors == []


def _write(tmp_path: Path, relative_path: str, content: str) -> None:
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_platform_contract_rejects_missing_system_capabilities(tmp_path: Path):
    """Product work must preserve framework-owned desktop and chat capabilities."""
    _write(
        tmp_path,
        "frontend/src/App.tsx",
        """
        export default function App() {
          return (
            <>
              <button onClick={() => toggle("chat")}>Chat</button>
            </>
          );
        }
        """,
    )
    _write(
        tmp_path,
        "frontend/src/components/AppViewer.tsx",
        "export function AppViewer() { return <DesktopAppComponent app={app} />; }",
    )
    _write(
        tmp_path,
        "frontend/src/components/ChatView.tsx",
        'fetch("/api/v1/chat", { body: JSON.stringify({ message: "broken" }) });',
    )
    _write(
        tmp_path,
        "backend/app/api/v1/chat.py",
        'router = APIRouter(prefix="/chat")\n@router.post("")\nasync def chat(): pass\n',
    )

    errors = _validate_platform_contract_files(tmp_path, "Build company discovery app")

    assert any("CostView.tsx" in error for error in errors)
    assert any("Desktop shell must preserve core system windows" in error for error in errors)
    assert any("JSON.stringify({ messages: history })" in error for error in errors)


def test_platform_contract_requires_backend_contract_for_mounted_competitive_intelligence(tmp_path: Path):
    """Mounted apps must keep their framework-owned backend contract alive."""
    _write(
        tmp_path,
        "frontend/src/App.tsx",
        """
        export default function App() {
          return (
            <>
              <button onClick={() => toggle("inception")}>New Inception</button>
              <button onClick={() => toggle("inceptions")}>Inceptions</button>
              <button onClick={() => toggle("timeline")}>Timeline</button>
              <button onClick={() => toggle("purpose")}>Purpose</button>
              <button onClick={() => toggle("tasks")}>Tasks</button>
              <button onClick={() => toggle("chat")}>Chat</button>
              <button onClick={() => toggle("architecture")}>Architecture</button>
              <button onClick={() => toggle("database")}>Database</button>
              <button onClick={() => toggle("cost")}>Cost</button>
              <button onClick={() => toggle("health")}>Health</button>
              <button onClick={() => toggle("settings")}>Settings</button>
              <AppWindow title="✦ Chat with the System" />
              <AppWindow title="Cost & Usage" />
            </>
          );
        }
        """,
    )
    _write(
        tmp_path,
        "frontend/src/components/AppViewer.tsx",
        "import { getDesktopAppComponent } from '../apps/registry';\nexport function AppViewer() { return <DesktopAppComponent app={app} />; }",
    )
    _write(
        tmp_path,
        "frontend/src/components/ChatView.tsx",
        'fetch("/api/v1/chat", { body: JSON.stringify({ messages: history }) });',
    )
    _write(
        tmp_path,
        "frontend/src/components/CostView.tsx",
        "export function CostView() { return <>Cost & Usage Spend Telemetry</>; }",
    )
    _write(
        tmp_path,
        "backend/app/api/v1/chat.py",
        'from fastapi import APIRouter\nrouter = APIRouter(prefix="/chat")\nclass ChatMessage: ...\nmessages: list[ChatMessage]\n@router.post("")\nasync def chat(): pass\n',
    )
    _write(
        tmp_path,
        "frontend/src/apps/competitive-intelligence/index.tsx",
        "export default function CompetitiveIntelligence() { return null; }",
    )

    errors = _validate_platform_contract_files(tmp_path, "Build competitive intelligence search")

    assert any("competitive_intelligence.py" in error for error in errors)


def test_platform_contract_allows_explicit_shell_redesign(tmp_path: Path):
    """Explicit shell work can bypass menu-marker preservation checks."""
    _write(
        tmp_path,
        "frontend/src/App.tsx",
        "export default function App() { return <main>custom desktop shell</main>; }",
    )
    _write(
        tmp_path,
        "frontend/src/components/AppViewer.tsx",
        "import { getDesktopAppComponent } from '../apps/registry';\nexport function AppViewer() { return <DesktopAppComponent app={app} />; }",
    )
    _write(
        tmp_path,
        "frontend/src/components/ChatView.tsx",
        'fetch("/api/v1/chat", { body: JSON.stringify({ messages: history }) });',
    )
    _write(
        tmp_path,
        "frontend/src/components/CostView.tsx",
        "export function CostView() { return <>Cost & Usage Spend Telemetry</>; }",
    )
    _write(
        tmp_path,
        "backend/app/api/v1/chat.py",
        'from fastapi import APIRouter\nrouter = APIRouter(prefix="/chat")\nclass ChatMessage: ...\nmessages: list[ChatMessage]\n@router.post("")\nasync def chat(): pass\n',
    )

    errors = _validate_platform_contract_files(tmp_path, "Redesign desktop shell and window manager")

    assert errors == []


def test_frontend_app_structure_rejects_noncanonical_module_root(tmp_path: Path):
    """Sandbox should reject CamelCase sibling roots when the slugged root exists."""
    _write(
        tmp_path,
        "frontend/src/apps/competitive-intelligence/index.tsx",
        "export default function CompetitiveIntelligence() { return null; }",
    )
    ctx = create_context("Improve competitive intelligence timeline")
    ctx = ctx.model_copy(
        update={
            "plan": EvolutionPlan(
                summary="Add timeline view",
                changes=[
                    FileChange(
                        file_path="frontend/src/apps/CompetitiveIntelligence/Timeline.tsx",
                        action="create",
                        description="Add timeline surface",
                        layer="frontend",
                    )
                ],
                requires_migration=False,
                risk_level="medium",
                reasoning="Add UI slice",
            ),
            "generated_files": [
                GeneratedFile(
                    file_path="frontend/src/apps/CompetitiveIntelligence/Timeline.tsx",
                    content="export function Timeline() { return null; }",
                    action="create",
                    layer="frontend",
                )
            ],
        }
    )

    errors = _validate_frontend_app_structure(tmp_path, ctx)

    assert any("canonical desktop slugs" in error for error in errors)
    assert any("frontend/src/apps/competitive-intelligence/" in error for error in errors)


def test_frontend_app_structure_detects_duplicate_module_roots(tmp_path: Path):
    """Sandbox should fail fast when the snapshot already contains conflicting app roots."""
    _write(
        tmp_path,
        "frontend/src/apps/CompanyDiscovery/index.tsx",
        "export default function CompanyDiscovery() { return null; }",
    )
    _write(
        tmp_path,
        "frontend/src/apps/company-discovery/index.tsx",
        "export default function CompanyDiscoveryCanonical() { return null; }",
    )
    ctx = create_context("Stabilize company discovery")

    errors = _validate_frontend_app_structure(tmp_path, ctx)

    assert any("multiple roots resolve to `company-discovery`" in error for error in errors)


def test_sandbox_prefers_evolved_app_source_when_present(tmp_path: Path):
    """Validation should copy the live evolved app when it exists."""
    managed_app = tmp_path / "managed_app"
    evolved_app = tmp_path / "evolved_app"
    (managed_app / "frontend").mkdir(parents=True)
    (managed_app / "backend").mkdir(parents=True)
    (evolved_app / "frontend").mkdir(parents=True)
    (evolved_app / "backend").mkdir(parents=True)

    sandbox = DockerSandbox.__new__(DockerSandbox)
    sandbox.config = EngineSettings(
        managed_app_path=managed_app,
        evolved_app_path=evolved_app,
    )

    assert sandbox._resolve_validation_source_path() == evolved_app.resolve()
