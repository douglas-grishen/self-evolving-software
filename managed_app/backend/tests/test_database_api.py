"""Tests for database schema and row browsing endpoints."""

import pytest
from fastapi import HTTPException

from app.api.v1.database import PAGE_SIZE, get_table_rows, get_tables


class _SchemaResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _CountResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _MappingRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _MappingRows(self._rows)


class _FakeConnection:
    def __init__(self, pk_columns):
        self._pk_columns = pk_columns

    async def run_sync(self, fn):
        return fn(_FakeSyncConnection(self._pk_columns))


class _FakeSyncInspector:
    def __init__(self, pk_columns):
        self._pk_columns = pk_columns

    def get_pk_constraint(self, table_name, schema=None):
        return {"constrained_columns": list(self._pk_columns.get(table_name, []))}


class _FakeSyncConnection:
    def __init__(self, pk_columns):
        self._pk_columns = pk_columns

    @property
    def inspector(self):
        return _FakeSyncInspector(self._pk_columns)


class _FakeAsyncSession:
    def __init__(self, *, schema_rows, table_rows, total_rows, pk_columns=None):
        self._schema_rows = schema_rows
        self._table_rows = table_rows
        self._total_rows = total_rows
        self._pk_columns = pk_columns or {}

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        if "information_schema.tables" in sql:
            return _SchemaResult(self._schema_rows)
        if sql.startswith("SELECT COUNT(*) FROM"):
            return _CountResult(self._total_rows)
        if sql.startswith("SELECT * FROM"):
            assert params == {"limit": PAGE_SIZE, "offset": 20}
            return _RowsResult(self._table_rows)
        raise AssertionError(f"Unexpected SQL: {sql}")

    async def connection(self):
        return _FakeConnection(self._pk_columns)


@pytest.fixture(autouse=True)
def patch_inspector(monkeypatch):
    """Keep PK lookup local to the fake connection instead of SQLAlchemy internals."""
    monkeypatch.setattr("app.api.v1.database.inspect", lambda conn: conn.inspector)


@pytest.mark.asyncio
async def test_get_tables_returns_columns_grouped_by_table():
    db = _FakeAsyncSession(
        schema_rows=[
            ("apps", "id", "uuid", "NO", None),
            ("apps", "name", "text", "YES", None),
            ("features", "id", "uuid", "NO", None),
        ],
        table_rows=[],
        total_rows=0,
    )

    response = await get_tables(db)

    assert response == {
        "tables": ["apps", "features"],
        "columns": {
            "apps": [
                {"name": "id", "type": "uuid", "nullable": False, "default": None},
                {"name": "name", "type": "text", "nullable": True, "default": None},
            ],
            "features": [
                {"name": "id", "type": "uuid", "nullable": False, "default": None},
            ],
        },
    }


@pytest.mark.asyncio
async def test_get_table_rows_returns_second_page_with_fixed_size():
    db = _FakeAsyncSession(
        schema_rows=[
            ("apps", "id", "uuid", "NO", None),
            ("apps", "name", "text", "YES", None),
        ],
        table_rows=[
            {"id": "app-21", "name": "App 21"},
            {"id": "app-22", "name": "App 22"},
        ],
        total_rows=22,
        pk_columns={"apps": ["id"]},
    )

    response = await get_table_rows("apps", page=2, db=db)

    assert response == {
        "table": "apps",
        "page": 2,
        "page_size": PAGE_SIZE,
        "total_rows": 22,
        "total_pages": 2,
        "rows": [
            {"id": "app-21", "name": "App 21"},
            {"id": "app-22", "name": "App 22"},
        ],
    }


@pytest.mark.asyncio
async def test_get_table_rows_rejects_unknown_table():
    db = _FakeAsyncSession(
        schema_rows=[("apps", "id", "uuid", "NO", None)],
        table_rows=[],
        total_rows=0,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_table_rows("missing_table", page=1, db=db)

    assert exc_info.value.status_code == 404
    assert "missing_table" in exc_info.value.detail
