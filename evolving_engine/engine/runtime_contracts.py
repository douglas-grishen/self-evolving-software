"""Runtime contract probes for mounted desktop apps.

The shared framework stays neutral: mounted-app probes and file-contract
requirements are loaded from the instance-local contracts YAML when present.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from engine.repo.scanner import extract_frontend_app_modules


_CORE_API_CONTRACT_PATH = Path(__file__).resolve().parents[2] / "core_api_contracts.yaml"
RUNTIME_PROBE_HEADER_NAME = "X-SES-Probe"
RUNTIME_PROBE_HEADER_VALUE = "runtime-contract"
RUNTIME_PROBE_HEADERS = {
    RUNTIME_PROBE_HEADER_NAME: RUNTIME_PROBE_HEADER_VALUE,
}


@dataclass(frozen=True)
class RuntimeContractProbe:
    """One HTTP request that must succeed for a mounted app contract."""

    app_key: str
    method: str
    path: str
    description: str
    expected_statuses: tuple[int, ...] = (200,)
    json_body: dict[str, Any] | None = None
    response_shape: str = "any"
    required_json_fields: tuple[str, ...] = ()
    required_list_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlatformFileContract:
    """Framework checks that a mounted app still exposes its expected backend file."""

    app_key: str
    trigger: str
    required_file: str
    markers: tuple[str, ...]
    description: str


def _default_core_framework_probes() -> tuple[RuntimeContractProbe, ...]:
    """Fallback core contract when the shared manifest cannot be loaded."""
    return (
        RuntimeContractProbe(
            app_key="framework",
            method="GET",
            path="/api/v1/health",
            description="Health route must stay mounted",
            expected_statuses=(200,),
            response_shape="object",
            required_json_fields=("status",),
        ),
        RuntimeContractProbe(
            app_key="framework",
            method="GET",
            path="/api/v1/system/info",
            description="System info route must stay mounted",
            expected_statuses=(200,),
            response_shape="object",
            required_json_fields=("ok", "status", "timestamp", "service"),
        ),
        RuntimeContractProbe(
            app_key="framework",
            method="GET",
            path="/api/v1/evolution/status",
            description="Evolution dashboard status route must stay mounted",
            expected_statuses=(200,),
            response_shape="object",
            required_json_fields=(
                "total_evolutions",
                "active_evolutions",
                "completed_evolutions",
                "failed_evolutions",
                "current_purpose_version",
                "pending_inceptions",
            ),
        ),
        RuntimeContractProbe(
            app_key="framework",
            method="GET",
            path="/api/v1/apps",
            description="Apps registry route must stay mounted",
            expected_statuses=(200,),
            response_shape="list",
        ),
        RuntimeContractProbe(
            app_key="framework",
            method="POST",
            path="/api/v1/apps",
            description="Apps creation route must remain available",
            expected_statuses=(201, 400, 409, 422),
            json_body={},
        ),
        RuntimeContractProbe(
            app_key="framework",
            method="POST",
            path="/api/v1/apps/capabilities",
            description="Capabilities creation route must remain available",
            expected_statuses=(201, 400, 409, 422),
            json_body={},
        ),
        RuntimeContractProbe(
            app_key="framework",
            method="GET",
            path="/api/v1/skills",
            description="Runtime skills registry route must stay mounted",
            expected_statuses=(200,),
        ),
        RuntimeContractProbe(
            app_key="framework",
            method="GET",
            path="/api/v1/skills/web-browser/schema",
            description="Runtime skill schema route must stay mounted",
            expected_statuses=(200, 404),
        ),
        RuntimeContractProbe(
            app_key="framework",
            method="POST",
            path="/api/v1/chat",
            description="Chat route must remain mounted",
            expected_statuses=(200, 400, 422),
            json_body={},
        ),
    )


def _default_core_availability_probes() -> tuple[RuntimeContractProbe, ...]:
    """Fallback availability probes when the shared manifest cannot be loaded."""
    return (
        RuntimeContractProbe(
            app_key="framework",
            method="GET",
            path="/api/v1/health",
            description="Canonical health endpoint",
            expected_statuses=(200,),
            response_shape="object",
            required_json_fields=("status",),
        ),
        RuntimeContractProbe(
            app_key="framework",
            method="GET",
            path="/api/v1/system/info",
            description="System info endpoint",
            expected_statuses=(200,),
            response_shape="object",
            required_json_fields=("ok", "status", "timestamp", "service"),
        ),
    )


def _load_core_contract_manifest() -> dict[str, Any]:
    """Load the shared core API contract manifest from the repo root."""
    if not _CORE_API_CONTRACT_PATH.exists():
        return {}

    data = yaml.safe_load(_CORE_API_CONTRACT_PATH.read_text()) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _probe_from_mapping(app_key: str, probe_data: dict[str, Any]) -> RuntimeContractProbe:
    """Build a runtime probe from a YAML mapping."""
    return RuntimeContractProbe(
        app_key=app_key,
        method=str(probe_data["method"]).upper(),
        path=str(probe_data["path"]),
        description=str(probe_data.get("description", f"{app_key} runtime contract")),
        expected_statuses=tuple(
            int(status) for status in probe_data.get("expected_statuses", [200])
        ),
        json_body=probe_data.get("json_body"),
        response_shape=str(probe_data.get("response_shape", "any")),
        required_json_fields=tuple(
            str(field) for field in probe_data.get("required_json_fields", [])
        ),
        required_list_fields=tuple(
            str(field) for field in probe_data.get("required_list_fields", [])
        ),
    )


def _load_core_probe_group(group: str) -> tuple[RuntimeContractProbe, ...]:
    """Load one named core probe group from the shared manifest."""
    manifest = _load_core_contract_manifest()
    entries = manifest.get(group, [])
    if not isinstance(entries, list):
        return ()
    return tuple(
        _probe_from_mapping("framework", entry)
        for entry in entries
        if isinstance(entry, dict)
    )


def get_core_framework_probes() -> tuple[RuntimeContractProbe, ...]:
    """Return framework-level routes that must remain mounted on every instance."""
    return _load_core_probe_group("core_probes") or _default_core_framework_probes()


def get_core_availability_probes() -> tuple[RuntimeContractProbe, ...]:
    """Return the canonical probes used to decide whether the backend is alive."""
    return _load_core_probe_group("availability_probes") or _default_core_availability_probes()


def _load_contract_apps(contracts_path: Path | None) -> dict[str, Any]:
    """Load the app contract mapping from an instance-local contracts file."""
    if contracts_path is None or not contracts_path.exists():
        return {}

    data = yaml.safe_load(contracts_path.read_text()) or {}
    apps = data.get("apps", {})
    if not isinstance(apps, dict):
        return {}
    return apps


def _load_runtime_contracts(
    contracts_path: Path | None,
) -> dict[str, tuple[RuntimeContractProbe, ...]]:
    """Load runtime contracts from an instance-local contracts file."""
    apps = _load_contract_apps(contracts_path)
    contracts: dict[str, tuple[RuntimeContractProbe, ...]] = {}

    for app_key, app_data in apps.items():
        probes: list[RuntimeContractProbe] = []
        for probe_data in app_data.get("probes", []):
            if not isinstance(probe_data, dict):
                continue
            probes.append(_probe_from_mapping(str(app_key), probe_data))
        contracts[str(app_key)] = tuple(probes)

    return contracts


def get_platform_file_contracts(
    contracts_path: Path | None = None,
) -> tuple[PlatformFileContract, ...]:
    """Return configured file-contract checks for mounted apps."""
    apps = _load_contract_apps(contracts_path)
    contracts: list[PlatformFileContract] = []

    for app_key, app_data in apps.items():
        contract_data = app_data.get("platform_contract")
        if not isinstance(contract_data, dict):
            continue

        required_file = contract_data.get("required_file")
        if not required_file:
            continue

        markers = tuple(str(marker) for marker in contract_data.get("markers", []))
        contracts.append(
            PlatformFileContract(
                app_key=str(app_key),
                trigger=str(
                    contract_data.get(
                        "trigger",
                        f"frontend/src/apps/{app_key}",
                    )
                ),
                required_file=str(required_file),
                markers=markers,
                description=str(
                    contract_data.get(
                        "description",
                        f"{app_key} platform contract",
                    )
                ),
            )
        )

    return tuple(contracts)


def get_runtime_contract_probes(
    app_path: Path,
    contracts_path: Path | None = None,
) -> list[RuntimeContractProbe]:
    """Return the expected runtime probes for mounted desktop apps in ``app_path``."""
    frontend_path = (
        app_path / "frontend" if (app_path / "frontend").exists() else app_path
    )
    modules, _ = extract_frontend_app_modules(frontend_path)
    contract_map = _load_runtime_contracts(contracts_path)

    probes: list[RuntimeContractProbe] = []
    seen: set[tuple[str, str]] = set()

    for module in modules:
        if not module.has_entrypoint:
            continue
        for probe in contract_map.get(module.canonical_key, ()):
            key = (probe.method, probe.path)
            if key in seen:
                continue
            probes.append(probe)
            seen.add(key)

    return probes


def validate_runtime_contract_response(
    probe: RuntimeContractProbe,
    response: httpx.Response,
) -> str | None:
    """Return ``None`` when a runtime contract probe response matches expectations."""
    if response.status_code not in probe.expected_statuses:
        return f"HTTP {response.status_code}"

    if (
        probe.response_shape == "any"
        and not probe.required_json_fields
        and not probe.required_list_fields
    ):
        return None

    try:
        payload = response.json()
    except ValueError as exc:
        return f"invalid JSON body: {exc}"

    if probe.response_shape == "list":
        if not isinstance(payload, list):
            return f"JSON body is not a list: {type(payload).__name__}"
        return None

    if probe.response_shape == "object" and not isinstance(payload, dict):
        return f"JSON body is not an object: {type(payload).__name__}"
    if not isinstance(payload, dict):
        return f"JSON body is not an object: {type(payload).__name__}"

    missing_fields = [
        field
        for field in probe.required_json_fields
        if field not in payload
    ]
    if missing_fields:
        return f"missing JSON fields: {', '.join(missing_fields)}"

    wrong_list_fields = [
        field
        for field in probe.required_list_fields
        if not isinstance(payload.get(field), list)
    ]
    if wrong_list_fields:
        return f"JSON fields must be arrays: {', '.join(wrong_list_fields)}"

    return None
