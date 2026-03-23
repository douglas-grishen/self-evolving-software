"""System settings API — runtime configuration for the engine."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.system_settings import SystemSetting
from app.schemas.system_settings import SettingResponse, SettingUpdate
from app.system_settings import (
    EDITABLE_SETTING_KEYS,
    SECRET_SETTING_KEYS,
    mask_setting_value,
    normalize_llm_provider,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=List[SettingResponse])
async def list_settings(db: AsyncSession = Depends(get_db)) -> list:
    """List all system settings."""
    result = await db.execute(select(SystemSetting).order_by(SystemSetting.key))
    settings = list(result.scalars().all())
    return [
        SettingResponse(
            key=setting.key,
            value=mask_setting_value(setting.key, setting.value),
            description=setting.description,
            updated_at=setting.updated_at,
        )
        for setting in settings
    ]


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
) -> SettingResponse:
    """Update a setting value. Only editable keys are allowed."""
    if key not in EDITABLE_SETTING_KEYS:
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
    elif key == "llm_provider":
        provider = normalize_llm_provider(payload.value)
        if provider != payload.value.strip().lower():
            raise HTTPException(
                status_code=422,
                detail="Provider must be one of: anthropic, bedrock, openai",
            )
        payload.value = provider
    elif key == "llm_model":
        payload.value = payload.value.strip()
        if not payload.value:
            raise HTTPException(status_code=422, detail="Model cannot be blank")
    elif key in SECRET_SETTING_KEYS:
        payload.value = payload.value.strip()

    setting.value = payload.value
    await db.flush()
    await db.refresh(setting)
    return SettingResponse(
        key=setting.key,
        value=mask_setting_value(setting.key, setting.value),
        description=setting.description,
        updated_at=setting.updated_at,
    )
