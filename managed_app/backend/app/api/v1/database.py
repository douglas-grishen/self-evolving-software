"""Database introspection endpoints — expose schema metadata and table data."""

from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter(prefix="/database", tags=["database"])
PAGE_SIZE = 20

_TABLES_AND_COLUMNS_SQL = text("""
    SELECT
        t.table_name,
        c.column_name,
        c.data_type,
        c.is_nullable,
        c.column_default
    FROM
        information_schema.tables t
    JOIN
        information_schema.columns c
        ON t.table_name = c.table_name
        AND t.table_schema = c.table_schema
    WHERE
        t.table_schema = 'public'
    ORDER BY
        t.table_name, c.ordinal_position
""")


def _quote_identifier(identifier: str) -> str:
    """Safely quote a SQL identifier after validating it exists in metadata."""
    return '"' + identifier.replace('"', '""') + '"'


async def _get_schema_map(db: AsyncSession) -> dict[str, list[dict[str, object | None]]]:
    """Load all public tables and their columns grouped by table name."""
    result = await db.execute(_TABLES_AND_COLUMNS_SQL)
    rows = result.fetchall()

    tables: dict[str, list[dict[str, object | None]]] = {}
    for table_name, col_name, data_type, is_nullable, default in rows:
        tables.setdefault(table_name, []).append(
            {
                "name": col_name,
                "type": data_type,
                "nullable": is_nullable == "YES",
                "default": default,
            }
        )

    return tables


async def _get_primary_key_columns(db: AsyncSession, table_name: str) -> list[str]:
    """Return primary-key columns for a table, preserving declared order."""
    connection = await db.connection()
    return await connection.run_sync(
        lambda sync_conn: inspect(sync_conn)
        .get_pk_constraint(table_name, schema="public")
        .get("constrained_columns")
        or []
    )


@router.get("/tables")
async def get_tables(db: AsyncSession = Depends(get_db)) -> dict:
    """Get list of all tables in the database with their columns."""
    try:
        tables = await _get_schema_map(db)
        return {
            "tables": sorted(tables.keys()),
            "columns": tables,
        }
    except Exception as e:
        return {
            "error": str(e),
            "tables": [],
            "columns": {},
        }


@router.get("/tables/{table_name}/rows")
async def get_table_rows(
    table_name: str,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return table rows paginated in fixed pages of 20 records."""
    try:
        tables = await _get_schema_map(db)
        if table_name not in tables:
            raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")

        column_names = [column["name"] for column in tables[table_name] if column["name"]]
        order_columns = await _get_primary_key_columns(db, table_name)
        if not order_columns and column_names:
            order_columns = [column_names[0]]

        quoted_table_name = _quote_identifier(table_name)
        order_by_clause = ""
        if order_columns:
            quoted_order_columns = ", ".join(_quote_identifier(name) for name in order_columns)
            order_by_clause = f" ORDER BY {quoted_order_columns}"

        offset = (page - 1) * PAGE_SIZE
        total_rows_result = await db.execute(text(f"SELECT COUNT(*) FROM {quoted_table_name}"))
        total_rows = total_rows_result.scalar_one()
        total_pages = max(1, ceil(total_rows / PAGE_SIZE))

        rows_result = await db.execute(
            text(
                f"SELECT * FROM {quoted_table_name}"
                f"{order_by_clause} LIMIT :limit OFFSET :offset"
            ),
            {"limit": PAGE_SIZE, "offset": offset},
        )
        rows = [dict(row) for row in rows_result.mappings().all()]

        return {
            "table": table_name,
            "page": page,
            "page_size": PAGE_SIZE,
            "total_rows": total_rows,
            "total_pages": total_pages,
            "rows": jsonable_encoder(rows),
        }
    except HTTPException:
        raise
    except Exception as e:
        return {
            "error": str(e),
            "table": table_name,
            "page": page,
            "page_size": PAGE_SIZE,
            "total_rows": 0,
            "total_pages": 1,
            "rows": [],
        }
