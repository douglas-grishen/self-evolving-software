from pathlib import Path

from app.api.v1 import _should_skip_dynamic_router_module


def test_should_skip_dynamic_router_module_for_legacy_imports(tmp_path: Path):
    module_path = tmp_path / "github_query_audit.py"
    module_path.write_text(
        "from app.core.settings import settings\nrouter = object()\n",
        encoding="utf-8",
    )

    should_skip, reason = _should_skip_dynamic_router_module(module_path)

    assert should_skip is True
    assert reason is not None
    assert "legacy import marker" in reason


def test_should_not_skip_dynamic_router_module_for_valid_router_source(tmp_path: Path):
    module_path = tmp_path / "catalog.py"
    module_path.write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n",
        encoding="utf-8",
    )

    should_skip, reason = _should_skip_dynamic_router_module(module_path)

    assert should_skip is False
    assert reason is None
