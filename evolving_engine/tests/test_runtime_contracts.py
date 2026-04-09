"""Tests for mounted app runtime contract probes."""

import httpx
from pathlib import Path

from engine.runtime_contracts import (
    RuntimeContractProbe,
    get_runtime_contract_probes,
    validate_runtime_contract_response,
)


def _write_contracts(path: Path) -> None:
    path.write_text(
        """
apps:
  example-app:
    platform_contract:
      required_file: backend/app/api/v1/example_app.py
      markers:
        - 'APIRouter(prefix="/example-app"'
        - '@router.get("/summary")'
        - '@router.post("/items/search")'
    probes:
      - method: GET
        path: /api/v1/example-app/summary
        description: Example App summary endpoint
        required_json_fields:
          - total_items
          - regions
      - method: POST
        path: /api/v1/example-app/items/search
        description: Example App item search endpoint
        json_body:
          page: 1
          page_size: 25
          filters:
            search_query: smoke-test
        required_json_fields:
          - items
          - total
          - page
          - page_size
        required_list_fields:
          - items
""".strip(),
        encoding="utf-8",
    )


def test_runtime_contract_probes_include_configured_smoke_checks(tmp_path):
    """Mounted apps should expose only the probes configured for the instance."""
    app_root = tmp_path / "managed_app"
    app_dir = app_root / "frontend" / "src" / "apps" / "example-app"
    app_dir.mkdir(parents=True)
    (app_dir / "index.tsx").write_text("export default function App() { return null; }")
    contracts_path = tmp_path / "contracts.yaml"
    _write_contracts(contracts_path)

    probes = get_runtime_contract_probes(app_root, contracts_path)

    assert [(probe.method, probe.path) for probe in probes] == [
        ("GET", "/api/v1/example-app/summary"),
        ("POST", "/api/v1/example-app/items/search"),
    ]
    assert probes[1].json_body == {
        "page": 1,
        "page_size": 25,
        "filters": {"search_query": "smoke-test"},
    }
    assert probes[0].required_json_fields == ("total_items", "regions")
    assert probes[1].required_json_fields == ("items", "total", "page", "page_size")
    assert probes[1].required_list_fields == ("items",)


def test_runtime_contract_probes_ignore_apps_without_instance_config(tmp_path):
    """The neutral framework should not invent probes for mounted apps."""
    app_root = tmp_path / "managed_app"
    app_dir = app_root / "frontend" / "src" / "apps" / "mystery-app"
    app_dir.mkdir(parents=True)
    (app_dir / "index.tsx").write_text("export default function App() { return null; }")

    assert get_runtime_contract_probes(app_root) == []


def test_runtime_contract_probes_can_be_loaded_from_instance_overlay_file(tmp_path):
    """Instance overlays should define mounted-app smoke checks without code changes."""
    app_root = tmp_path / "managed_app"
    app_dir = app_root / "frontend" / "src" / "apps" / "market-radar"
    app_dir.mkdir(parents=True)
    (app_dir / "index.tsx").write_text("export default function App() { return null; }")

    contracts_path = tmp_path / "contracts.yaml"
    contracts_path.write_text(
        """
apps:
  market-radar:
    probes:
      - method: GET
        path: /api/v1/market-radar/summary
        description: Market Radar summary endpoint
        required_json_fields:
          - regions
          - categories
""".strip()
    )

    probes = get_runtime_contract_probes(app_root, contracts_path)

    assert [(probe.method, probe.path) for probe in probes] == [
        ("GET", "/api/v1/market-radar/summary"),
    ]
    assert probes[0].required_json_fields == ("regions", "categories")


def test_validate_runtime_contract_response_rejects_shape_drift():
    """200 responses still fail when the body no longer matches the framework contract."""
    search_probe = RuntimeContractProbe(
        app_key="example-app",
        method="POST",
        path="/api/v1/example-app/items/search",
        description="Example App item search endpoint",
        required_json_fields=("items", "total", "page", "page_size"),
        required_list_fields=("items",),
    )

    response = httpx.Response(
        200,
        json={
            "results": [],
            "total": 0,
            "page": 1,
            "page_size": 25,
            "has_more": False,
        },
    )

    assert validate_runtime_contract_response(search_probe, response) == "missing JSON fields: items"
