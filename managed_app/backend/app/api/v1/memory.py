"""Engine Memory API — inter-session lessons learned for the evolving engine.

Engine-facing (internal Docker network, no auth required):
  GET  /memory             — fetch all active lessons (called at start of every cycle)
  POST /memory             — create a new lesson (called after failure analysis)
  PATCH /memory/{id}       — update or reinforce a lesson

UI-facing (same endpoints, used by the dashboard):
  GET  /memory             — list lessons (active_only=false for full history)
  DELETE /memory/{id}      — soft-disable a lesson (never hard-deletes)

This router is auto-discovered by the pkgutil loop in app/api/v1/__init__.py —
no changes to __init__.py are needed.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.memory import EngineMemory
from app.schemas.memory import MemoryCreate, MemoryPatch, MemoryResponse

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("", response_model=List[MemoryResponse])
async def list_lessons(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
) -> list:
    """List lessons sorted by relevance (times_reinforced desc, then newest first).

    The engine calls this with active_only=True (default).
    The UI may call with active_only=False to show the full history.
    """
    query = select(EngineMemory).order_by(
        desc(EngineMemory.times_reinforced),
        desc(EngineMemory.created_at),
    )
    if active_only:
        query = query.where(EngineMemory.active.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("", response_model=MemoryResponse, status_code=201)
async def create_lesson(
    payload: MemoryCreate,
    db: AsyncSession = Depends(get_db),
) -> EngineMemory:
    """Create a new lesson.

    Called by the engine after detecting a failure pattern, or by a human via the UI.
    """
    record = EngineMemory(**payload.model_dump())
    db.add(record)
    await db.flush()
    await db.refresh(record)
    return record


@router.patch("/{lesson_id}", response_model=MemoryResponse)
async def patch_lesson(
    lesson_id: str,
    payload: MemoryPatch,
    db: AsyncSession = Depends(get_db),
) -> EngineMemory:
    """Update or reinforce an existing lesson.

    The engine calls this to increment times_reinforced when it detects the same
    mistake happening again.
    """
    result = await db.execute(
        select(EngineMemory).where(EngineMemory.id == lesson_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Lesson not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(record, field, value)
    await db.flush()
    await db.refresh(record)
    return record


@router.delete("/{lesson_id}", status_code=204)
async def disable_lesson(
    lesson_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-disable a lesson (sets active=False).

    Hard deletes are never performed — lessons are kept for audit purposes.
    Use PATCH with active=True to re-enable.
    """
    result = await db.execute(
        select(EngineMemory).where(EngineMemory.id == lesson_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Lesson not found")
    record.active = False
    await db.flush()
