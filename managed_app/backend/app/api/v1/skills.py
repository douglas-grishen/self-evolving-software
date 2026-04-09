"""Runtime skills API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.skills import SkillRecord
from app.models.system_settings import SystemSetting
from app.schemas.skills import (
    SkillInvocationRequest,
    SkillInvocationResponse,
    SkillResponse,
    SkillSchemaResponse,
)
from app.skills_runtime import (
    SkillDisabledError,
    SkillExecutor,
    SkillNotFoundError,
    SkillValidationError,
)

router = APIRouter(prefix="/skills", tags=["skills"])

_EXECUTOR = SkillExecutor()


async def ensure_default_skills(db: AsyncSession) -> None:
    """Backfill code-defined skills into persistent storage."""
    result = await db.execute(select(SkillRecord))
    existing = {record.key: record for record in result.scalars().all()}
    changed = False

    for skill in _EXECUTOR.registry.list_skills():
        metadata = skill.metadata()
        record = existing.get(metadata.key)
        if record is None:
            db.add(
                SkillRecord(
                    key=metadata.key,
                    name=metadata.name,
                    description=metadata.description,
                    status=metadata.status,
                    scope=metadata.scope,
                    executor_kind=metadata.executor_kind,
                    config_json=metadata.config_json,
                    permissions_json=metadata.permissions_json,
                )
            )
            changed = True
            continue

        if not record.name:
            record.name = metadata.name
            changed = True
        if not record.description:
            record.description = metadata.description
            changed = True
        if not record.scope:
            record.scope = metadata.scope
            changed = True
        if not record.executor_kind:
            record.executor_kind = metadata.executor_kind
            changed = True
        if record.config_json is None:
            record.config_json = metadata.config_json
            changed = True
        if record.permissions_json is None:
            record.permissions_json = metadata.permissions_json
            changed = True

    if changed:
        await db.commit()


def _skill_response(record: SkillRecord) -> SkillResponse:
    return SkillResponse(
        id=record.id,
        key=record.key,
        name=record.name,
        description=record.description,
        status=record.status,
        scope=record.scope,
        executor_kind=record.executor_kind,
        config_json=record.config_json or {},
        permissions_json=record.permissions_json or {},
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


async def _load_skill_record(db: AsyncSession, key: str) -> SkillRecord:
    result = await db.execute(select(SkillRecord).where(SkillRecord.key == key))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Skill '{key}' not found")
    return record


async def _load_skill_settings(db: AsyncSession) -> dict[str, str]:
    result = await db.execute(
        select(SystemSetting.key, SystemSetting.value).where(
            SystemSetting.key.like("skill\\_%", escape="\\")
        )
    )
    return {key: value or "" for key, value in result.all()}


@router.get("", response_model=list[SkillResponse])
async def list_skills(db: AsyncSession = Depends(get_db)) -> list[SkillResponse]:
    result = await db.execute(select(SkillRecord).order_by(SkillRecord.key))
    return [_skill_response(record) for record in result.scalars().all()]


@router.get("/{skill_key}", response_model=SkillResponse)
async def get_skill(skill_key: str, db: AsyncSession = Depends(get_db)) -> SkillResponse:
    return _skill_response(await _load_skill_record(db, skill_key))


@router.get("/{skill_key}/schema", response_model=SkillSchemaResponse)
async def get_skill_schema(
    skill_key: str,
    db: AsyncSession = Depends(get_db),
) -> SkillSchemaResponse:
    record = await _load_skill_record(db, skill_key)
    try:
        skill = _EXECUTOR.registry.get(skill_key)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SkillSchemaResponse(skill=_skill_response(record), input_schema=skill.input_schema())


@router.post("/{skill_key}/invoke", response_model=SkillInvocationResponse)
async def invoke_skill(
    skill_key: str,
    payload: SkillInvocationRequest,
    db: AsyncSession = Depends(get_db),
) -> SkillInvocationResponse:
    record = await _load_skill_record(db, skill_key)
    settings_map = await _load_skill_settings(db)
    try:
        response = await _EXECUTOR.invoke(
            record,
            payload,
            settings_map=settings_map,
        )
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillDisabledError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SkillValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SkillInvocationResponse.model_validate(response.model_dump())
