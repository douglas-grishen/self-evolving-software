"""Monitoring endpoints — the Evolution Plane's window into runtime state.

These endpoints expose the Operational Plane's health, metrics, schema, and logs
so the MAPE-K engine can observe what is happening at runtime and decide
whether the system needs to evolve.

All endpoints are read-only and intended for internal (control-plane) access.
"""

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import engine as db_engine, get_db
from app.middleware.metrics import get_metrics_snapshot

router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.get("/health")
async def detailed_health(db: AsyncSession = Depends(get_db)) -> dict:
    """Deep health check including database connectivity and latency.

    Used by the engine's Monitor phase to detect degradation or failures.
    """
    checks = {
        "app": "ok",
        "database": "unknown",
    }

    # Database connectivity + latency
    db_latency_ms = None
    try:
        start = time.monotonic()
        await db.execute(text("SELECT 1"))
        db_latency_ms = round((time.monotonic() - start) * 1000, 2)
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"

    return {
        "status": overall,
        "checks": checks,
        "db_latency_ms": db_latency_ms,
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "environment": settings.environment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/metrics")
async def runtime_metrics() -> dict:
    """Request-level metrics: counts, error rates, response times.

    Used by the engine's Monitor phase to detect performance regressions,
    error spikes, or anomalous patterns that might require code evolution.
    """
    snapshot = get_metrics_snapshot()
    return {
        "period": "since_startup",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **snapshot,
    }


@router.get("/schema")
async def database_schema() -> dict:
    """Current database schema: tables, columns, types, constraints.

    Used by the engine's Analyze phase to understand the data model before
    generating migrations or modifying database-related code.
    """
    tables = []

    async with db_engine.connect() as conn:

        def _inspect(sync_conn):
            inspector = inspect(sync_conn)
            result = []
            for table_name in inspector.get_table_names():
                columns = []
                for col in inspector.get_columns(table_name):
                    columns.append(
                        {
                            "name": col["name"],
                            "type": str(col["type"]),
                            "nullable": col.get("nullable", True),
                            "default": str(col["default"]) if col.get("default") else None,
                            "primary_key": col.get("autoincrement", False)
                            or col["name"] == "id",
                        }
                    )

                indexes = [
                    {"name": idx["name"], "columns": idx["column_names"], "unique": idx["unique"]}
                    for idx in inspector.get_indexes(table_name)
                ]

                foreign_keys = [
                    {
                        "column": fk["constrained_columns"],
                        "references": f"{fk['referred_table']}.{fk['referred_columns']}",
                    }
                    for fk in inspector.get_foreign_keys(table_name)
                ]

                result.append(
                    {
                        "name": table_name,
                        "columns": columns,
                        "indexes": indexes,
                        "foreign_keys": foreign_keys,
                    }
                )
            return result

        tables = await conn.run_sync(_inspect)

    return {
        "tables": tables,
        "table_count": len(tables),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/errors")
async def recent_errors() -> dict:
    """Recent application errors captured by the error tracking middleware.

    Used by the engine's Monitor phase to detect bugs, exceptions, or
    unexpected behaviors that the system should fix autonomously.
    """
    from app.middleware.metrics import get_recent_errors

    errors = get_recent_errors()
    return {
        "errors": errors,
        "count": len(errors),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
