"""Evolution API — bridge between the engine, the UI, and external systems.

Engine-facing endpoints (control-plane):
  POST /events              — engine reports evolution lifecycle events
  PUT  /events/{request_id} — engine updates an evolution's final status
  GET  /inceptions?status=  — engine polls for pending inceptions
  PUT  /inceptions/{id}     — engine marks inception as applied/rejected
  POST /purpose             — engine stores a purpose version

UI-facing endpoints (managed-system):
  GET  /events              — evolution history (paginated)
  GET  /events/{request_id} — single evolution detail
  GET  /inceptions          — inception history
  POST /inceptions          — submit a new inception
  GET  /purpose             — current purpose
  GET  /purpose/history     — all purpose versions
  GET  /status              — dashboard summary
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.database import get_db
from app.models.admin import AdminUser
from app.models.evolution import EvolutionEventRecord, InceptionRecord, PurposeRecord
from app.schemas.evolution import (
    DashboardStatusResponse,
    EvolutionEventCreate,
    EvolutionEventResponse,
    InceptionCreate,
    InceptionResponse,
    InceptionUpdate,
    PurposeCreate,
    PurposeResponse,
)

router = APIRouter(prefix="/evolution", tags=["evolution"])


# ---------------------------------------------------------------------------
# Evolution Events
# ---------------------------------------------------------------------------


@router.post("/events", response_model=EvolutionEventResponse, status_code=201)
async def create_evolution_event(
    payload: EvolutionEventCreate,
    db: AsyncSession = Depends(get_db),
) -> EvolutionEventRecord:
    """Engine reports a new or updated evolution event."""
    # Upsert: if request_id exists, update; otherwise create
    result = await db.execute(
        select(EvolutionEventRecord).where(
            EvolutionEventRecord.request_id == payload.request_id
        )
    )
    record = result.scalar_one_or_none()

    if record:
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(record, field, value)
    else:
        record = EvolutionEventRecord(**payload.model_dump())
        db.add(record)

    await db.flush()
    return record


@router.put("/events/{request_id}", response_model=EvolutionEventResponse)
async def update_evolution_event(
    request_id: str,
    payload: EvolutionEventCreate,
    db: AsyncSession = Depends(get_db),
) -> EvolutionEventRecord:
    """Engine updates an existing evolution event (e.g., final status)."""
    result = await db.execute(
        select(EvolutionEventRecord).where(
            EvolutionEventRecord.request_id == request_id
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Evolution event not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(record, field, value)

    await db.flush()
    return record


@router.get("/events", response_model=List[EvolutionEventResponse])
async def list_evolution_events(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> List[EvolutionEventRecord]:
    """List evolution events, most recent first."""
    result = await db.execute(
        select(EvolutionEventRecord)
        .order_by(desc(EvolutionEventRecord.created_at))
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


@router.get("/events/{request_id}", response_model=EvolutionEventResponse)
async def get_evolution_event(
    request_id: str,
    db: AsyncSession = Depends(get_db),
) -> EvolutionEventRecord:
    """Get a single evolution event by request_id."""
    result = await db.execute(
        select(EvolutionEventRecord).where(
            EvolutionEventRecord.request_id == request_id
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Evolution event not found")
    return record


# ---------------------------------------------------------------------------
# Inceptions
# ---------------------------------------------------------------------------


@router.post("/inceptions", response_model=InceptionResponse, status_code=201)
async def create_inception(
    payload: InceptionCreate,
    db: AsyncSession = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin),
) -> InceptionRecord:
    """Submit a new inception (requires admin authentication)."""
    record = InceptionRecord(
        source=payload.source,
        directive=payload.directive,
        rationale=payload.rationale,
        status="pending",
    )
    db.add(record)
    await db.flush()
    return record


@router.get("/inceptions", response_model=List[InceptionResponse])
async def list_inceptions(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
) -> List[InceptionRecord]:
    """List inceptions, optionally filtered by status."""
    query = select(InceptionRecord).order_by(desc(InceptionRecord.submitted_at)).limit(limit)
    if status:
        query = query.where(InceptionRecord.status == status)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.put("/inceptions/{inception_id}", response_model=InceptionResponse)
async def update_inception(
    inception_id: str,
    payload: InceptionUpdate,
    db: AsyncSession = Depends(get_db),
) -> InceptionRecord:
    """Engine updates an inception status (processing, applied, rejected)."""
    result = await db.execute(
        select(InceptionRecord).where(InceptionRecord.id == inception_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Inception not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(record, field, value)

    if payload.status in ("applied", "rejected") and not record.processed_at:
        record.processed_at = datetime.now(timezone.utc)

    await db.flush()
    return record


# ---------------------------------------------------------------------------
# Purpose
# ---------------------------------------------------------------------------


@router.post("/purpose", response_model=PurposeResponse, status_code=201)
async def create_purpose(
    payload: PurposeCreate,
    db: AsyncSession = Depends(get_db),
) -> PurposeRecord:
    """Engine stores a new purpose version."""
    record = PurposeRecord(
        version=payload.version,
        content_yaml=payload.content_yaml,
        inception_id=payload.inception_id,
    )
    db.add(record)
    await db.flush()
    return record


@router.get("/purpose", response_model=Optional[PurposeResponse])
async def get_current_purpose(
    db: AsyncSession = Depends(get_db),
) -> Optional[PurposeRecord]:
    """Get the current (highest version) purpose."""
    result = await db.execute(
        select(PurposeRecord).order_by(desc(PurposeRecord.version)).limit(1)
    )
    return result.scalar_one_or_none()


@router.get("/purpose/history", response_model=List[PurposeResponse])
async def list_purpose_history(
    db: AsyncSession = Depends(get_db),
) -> List[PurposeRecord]:
    """List all purpose versions, newest first."""
    result = await db.execute(
        select(PurposeRecord).order_by(desc(PurposeRecord.version))
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Dashboard Status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=DashboardStatusResponse)
async def dashboard_status(
    db: AsyncSession = Depends(get_db),
) -> DashboardStatusResponse:
    """Aggregated dashboard data for the Evolution Monitor UI."""
    # Count evolutions by status
    total = await db.execute(select(func.count()).select_from(EvolutionEventRecord))
    total_count = total.scalar() or 0

    active = await db.execute(
        select(func.count())
        .select_from(EvolutionEventRecord)
        .where(EvolutionEventRecord.status.notin_(["completed", "failed"]))
    )
    active_count = active.scalar() or 0

    completed = await db.execute(
        select(func.count())
        .select_from(EvolutionEventRecord)
        .where(EvolutionEventRecord.status == "completed")
    )
    completed_count = completed.scalar() or 0

    failed = await db.execute(
        select(func.count())
        .select_from(EvolutionEventRecord)
        .where(EvolutionEventRecord.status == "failed")
    )
    failed_count = failed.scalar() or 0

    # Current purpose version
    purpose_result = await db.execute(
        select(PurposeRecord.version).order_by(desc(PurposeRecord.version)).limit(1)
    )
    current_purpose_version = purpose_result.scalar_one_or_none()

    # Pending inceptions
    pending = await db.execute(
        select(func.count())
        .select_from(InceptionRecord)
        .where(InceptionRecord.status == "pending")
    )
    pending_count = pending.scalar() or 0

    # Last evolution
    last_result = await db.execute(
        select(EvolutionEventRecord)
        .order_by(desc(EvolutionEventRecord.created_at))
        .limit(1)
    )
    last_record = last_result.scalar_one_or_none()

    return DashboardStatusResponse(
        total_evolutions=total_count,
        active_evolutions=active_count,
        completed_evolutions=completed_count,
        failed_evolutions=failed_count,
        current_purpose_version=current_purpose_version,
        pending_inceptions=pending_count,
        last_evolution=EvolutionEventResponse.model_validate(last_record) if last_record else None,
    )
