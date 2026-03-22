"""Tests for sandbox plan contract validation."""

from engine.context import create_context
from engine.models.evolution import EvolutionPlan, FileChange, GeneratedFile
from engine.sandbox.docker_sandbox import _validate_plan_contract


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
