"""Shared core API contract regression tests."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.main import app


_CORE_CONTRACT_PATH = Path(__file__).resolve().parents[3] / "core_api_contracts.yaml"


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _ScalarsList:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _ListResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _ScalarsList(self._items)

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _AppsListDB:
    async def execute(self, _statement):
        return _ListResult(
            [
                SimpleNamespace(
                    id="app-1",
                    name="Delegate Setup",
                    icon="bot",
                    status="active",
                    goal="Guide connection setup",
                    features=[],
                    capabilities=[],
                )
            ]
        )


class _EvolutionStatusDB:
    def __init__(self):
        self._calls = 0

    async def execute(self, _statement):
        self._calls += 1
        if self._calls == 1:
            return _ScalarResult(3)
        if self._calls == 2:
            return _ScalarResult(0)
        if self._calls == 3:
            return _ScalarResult(2)
        if self._calls == 4:
            return _ScalarResult(1)
        if self._calls == 5:
            return _ScalarResult(4)
        if self._calls == 6:
            return _ScalarResult(0)
        return _ScalarResult(None)


def _load_core_contracts() -> list[dict]:
    data = yaml.safe_load(_CORE_CONTRACT_PATH.read_text(encoding="utf-8")) or {}
    return list(data.get("core_probes", []))


def _assert_probe_response(probe: dict, response) -> None:
    assert response.status_code in probe["expected_statuses"]
    assert response.status_code not in {307, 308}

    shape = probe.get("response_shape", "any")
    if shape == "any":
        return

    payload = response.json()
    if shape == "list":
        assert isinstance(payload, list)
        return

    assert isinstance(payload, dict)
    for field in probe.get("required_json_fields", []):
        assert field in payload


@pytest.fixture
async def client():
    async def override_db(request: Request):
        if request.url.path == "/api/v1/apps" and request.method == "GET":
            yield _AppsListDB()
            return
        if request.url.path == "/api/v1/evolution/status":
            yield _EvolutionStatusDB()
            return
        yield _AppsListDB()

    app.dependency_overrides[get_db] = override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_core_api_contract_manifest_matches_backend_routes(client: AsyncClient):
    """The backend must satisfy the same core contract the engine uses in deploy checks."""
    for probe in _load_core_contracts():
        response = await client.request(
            probe["method"],
            probe["path"],
            json=probe.get("json_body"),
        )
        _assert_probe_response(probe, response)
