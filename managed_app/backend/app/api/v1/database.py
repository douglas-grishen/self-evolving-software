"""Database introspection endpoint — exposes schema metadata."""

from fastapi import APIRouter, Depends
from sqlalchemy import text, inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter(prefix="/database", tags=["database"])


@router.get("/tables")
async def get_tables(db: AsyncSession = Depends(get_db)) -> dict:
    """Get list of all tables in the database with their columns."""
    try:
        # Get connection for introspection
        async with db.begin():
            # Use raw SQL to get tables and columns
            result = await db.execute(
                text("""
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
            )

            rows = result.fetchall()

            # Organize by table
            tables = {}
            for row in rows:
                table_name, col_name, data_type, is_nullable, default = row
                if table_name not in tables:
                    tables[table_name] = []
                tables[table_name].append({
                    "name": col_name,
                    "type": data_type,
                    "nullable": is_nullable,
                    "default": default
                })

            return {
                "tables": sorted(tables.keys()),
                "columns": tables
            }
    except Exception as e:
        return {
            "error": str(e),
            "tables": [],
            "columns": {}
        }
