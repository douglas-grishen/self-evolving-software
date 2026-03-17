"""System settings API — runtime configuration for the engine."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.system_settings import SystemSetting
from app.schemas.system_settings import SettingResponse, SettingUpdate

router = APIRouter(prefix="/settings", tags=["settings"])

_EDITABLE_KEYS = {"proactive_interval_minutes", "anthropic_api_key"}


@router.get("", response_model=List[SettingResponse])
async def list_settings(db: AsyncSession = Depends(get_db)) -> list:
    """List all system settings."""
    result = await db.execute(select(SystemSetting).order_by(SystemSetting.key))
    settings = list(result.scalars().all())
    # Mask the API key value
    out = []
    for s in settings:
        if s.key == "anthropic_api_key" and s.value:
            masked = "*" * max(0, len(s.value) - 4) + s.value[-4:]
            out.append(SettingResponse(key=s.key, value=masked, description=s.description, updated_at=s.updated_at))
        else:
            out.append(SettingResponse(key=s.key, value=s.value, description=s.description, updated_at=s.updated_at))
    return out


@router.get("/{key}", response_model=SettingResponse)
async def get_setting(key: str, db: AsyncSession = Depends(get_db)) -> SystemSetting:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
    return setting


@router.put("/{key}", response_model=SettingResponse)
async def update_setting(
    key: str,
    payload: SettingUpdate,
    db: AsyncSession = Depends(get_db),
) -> SystemSetting:
    """Update a setting value. Only editable keys are allowed."""
    if key not in _EDITABLE_KEYS:
        raise HTTPException(status_code=403, detail=f"Setting '{key}' is not editable via API")

    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")

    # Validate proactive interval
    if key == "proactive_interval_minutes":
        try:
            v = int(payload.value)
            if not (5 <= v <= 1440):
                raise HTTPException(status_code=422, detail="Interval must be between 5 and 1440 minutes")
        except ValueError:
            raise HTTPException(status_code=422, detail="Interval must be an integer")

    setting.value = payload.value
    await db.flush()
    await db.refresh(setting)
    return setting
