"""Tests for app and feature creation flows used by the engine."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.apps import add_feature, create_app
from app.models.apps import (
    AppRecord,
    CapabilityRecord,
    FeatureRecord,
    app_capabilities,
    feature_capabilities,
)
from app.schemas.apps import AppCreate, FeatureCreate


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _ExecuteResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows
        self._scalar = scalar

    def scalars(self):
        return _ScalarResult(self._rows)

    def scalar_one_or_none(self):
        return self._scalar


class _FakeAsyncSession:
    def __init__(self, *, loaded_app=None, existing_app=None, loaded_feature=None):
        self.added = []
        self.association_inserts = []
        self._id_counter = 0
        self._app_select_count = 0
        self.loaded_app = loaded_app
        self.existing_app = existing_app
        self.loaded_feature = loaded_feature
        self._capabilities = [
            SimpleNamespace(id="cap-1"),
            SimpleNamespace(id="cap-2"),
        ]

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                self._id_counter += 1
                obj.id = f"id-{self._id_counter}"

    async def execute(self, stmt):
        if getattr(stmt, "table", None) in {app_capabilities, feature_capabilities}:
            self.association_inserts.append(stmt)
            return _ExecuteResult([])

        entity = None
        if getattr(stmt, "column_descriptions", None):
            entity = stmt.column_descriptions[0].get("entity")

        if entity is CapabilityRecord:
            return _ExecuteResult(self._capabilities)
        if entity is AppRecord:
            self._app_select_count += 1
            if self.existing_app is not None:
                return _ExecuteResult([], scalar=self.existing_app)
            if self.loaded_app is not None and self._app_select_count > 1:
                return _ExecuteResult([], scalar=self.loaded_app)
            return _ExecuteResult([], scalar=None)
        if entity is FeatureRecord:
            return _ExecuteResult([], scalar=self.loaded_feature)

        return _ExecuteResult([])

    async def refresh(self, obj, attribute_names=None):
        return None

    async def rollback(self):
        return None


@pytest.mark.asyncio
async def test_create_app_inserts_capability_links_without_lazy_loading():
    """Engine app registration should use explicit association inserts under async SQLAlchemy."""
    loaded_app = AppRecord(name="Delegate Setup")
    loaded_app.id = "loaded-app"
    db = _FakeAsyncSession(loaded_app=loaded_app)

    payload = AppCreate(
        name="Delegate Setup",
        icon="🛂",
        goal="Configure delegation",
        status="building",
        capability_ids=["cap-1", "cap-2"],
        features=[
            {
                "name": "Settings",
                "description": "Manage settings",
                "user_facing_description": "Manage settings",
                "capability_ids": ["cap-1", "cap-2"],
            }
        ],
        metadata_json={"frontend_entry": "delegate-setup"},
    )

    app = await create_app(payload, db)

    assert app is loaded_app
    tables = [stmt.table.name for stmt in db.association_inserts]
    assert tables.count("app_capabilities") == 2
    assert tables.count("feature_capabilities") == 2


@pytest.mark.asyncio
async def test_add_feature_returns_reloaded_feature_with_capabilities():
    """Feature creation should reload the feature so response serialization never lazy-loads."""
    existing_app = AppRecord(name="Delegate Setup")
    existing_app.id = "app-1"
    loaded_feature = FeatureRecord(
        app_id="app-1",
        name="Approvals",
        description="Review approvals",
        user_facing_description="Review approvals",
    )
    loaded_feature.id = "feature-1"
    db = _FakeAsyncSession(existing_app=existing_app, loaded_feature=loaded_feature)

    payload = FeatureCreate(
        name="Approvals",
        description="Review approvals",
        user_facing_description="Review approvals",
        capability_ids=["cap-1", "cap-2"],
    )

    feature = await add_feature("app-1", payload, db)

    assert feature is loaded_feature
    tables = [stmt.table.name for stmt in db.association_inserts]
    assert tables.count("feature_capabilities") == 2


@pytest.mark.asyncio
async def test_create_app_rejects_duplicate_name():
    """Duplicate app names should return a clear 409 instead of bubbling into a 500."""
    existing_app = AppRecord(name="Delegate Setup")
    existing_app.id = "app-1"
    db = _FakeAsyncSession(existing_app=existing_app)

    payload = AppCreate(
        name="Delegate Setup",
        goal="Configure delegation",
        status="building",
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_app(payload, db)

    assert exc_info.value.status_code == 409
    assert "already exists" in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_app_rejects_invalid_status():
    """Invalid app status values should fail fast with a client-facing 400."""
    db = _FakeAsyncSession()

    payload = AppCreate(
        name="Delegate Setup",
        goal="Configure delegation",
        status="not-a-real-status",
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_app(payload, db)

    assert exc_info.value.status_code == 400
    assert "Invalid app status" in exc_info.value.detail
