"""System settings API — runtime configuration for chat and the engine."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.system_settings import SystemSetting
from app.schemas.system_settings import SettingResponse, SettingUpdate
from app.system_settings import (
    EDITABLE_SETTING_KEYS,
    ENGINE_BUDGET_SETTING_KEYS,
    MODEL_SETTING_KEYS,
    PROVIDER_SETTING_KEYS,
    SECRET_SETTING_KEYS,
    mask_setting_value,
    normalize_llm_provider,
)

router = APIRouter(prefix="/settings", tags=["settings"])
_BOOLEAN_SETTING_KEYS = {"skill_browser_enabled", "skill_email_enabled"}
_INTEGER_SETTING_KEYS = {"skill_browser_timeout_seconds"}


@router.get("", response_model=list[SettingResponse])
async def list_settings(db: AsyncSession = Depends(get_db)) -> list:
    """List all system settings."""
    result = await db.execute(
        select(
            SystemSetting.key,
            SystemSetting.value,
            SystemSetting.description,
            SystemSetting.updated_at,
        ).order_by(SystemSetting.key)
    )
    settings = result.all()
    return [
        SettingResponse(
            key=key,
            value=mask_setting_value(key, value or ""),
            description=description,
            updated_at=updated_at,
        )
        for key, value, description, updated_at in settings
    ]


@router.get("/{key}", response_model=SettingResponse)
async def get_setting(key: str, db: AsyncSession = Depends(get_db)) -> SettingResponse:
    result = await db.execute(
        select(
            SystemSetting.key,
            SystemSetting.value,
            SystemSetting.description,
            SystemSetting.updated_at,
        ).where(SystemSetting.key == key)
    )
    setting = result.one_or_none()
    if not setting:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
    setting_key, value, description, updated_at = setting
    return SettingResponse(
        key=setting_key,
        value=value or "",
        description=description,
        updated_at=updated_at,
    )


@router.put("/{key}", response_model=SettingResponse)
async def update_setting(
    key: str,
    payload: SettingUpdate,
    db: AsyncSession = Depends(get_db),
) -> SettingResponse:
    """Update a setting value. Only editable keys are allowed."""
    if key not in EDITABLE_SETTING_KEYS:
        raise HTTPException(status_code=403, detail=f"Setting '{key}' is not editable via API")

    result = await db.execute(select(SystemSetting.key).where(SystemSetting.key == key))
    setting_key = result.scalar_one_or_none()
    if not setting_key:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")

    # Validate proactive interval
    if key == "proactive_interval_minutes":
        try:
            v = int(payload.value)
            if not (5 <= v <= 1440):
                raise HTTPException(
                    status_code=422,
                    detail="Interval must be between 5 and 1440 minutes",
                )
        except ValueError:
            raise HTTPException(status_code=422, detail="Interval must be an integer")
    elif key in ENGINE_BUDGET_SETTING_KEYS:
        try:
            v = int(payload.value)
            if v < 1:
                raise HTTPException(status_code=422, detail="Budget limit must be at least 1")
        except ValueError:
            raise HTTPException(status_code=422, detail="Budget limit must be an integer")
        payload.value = str(v)
    elif key in PROVIDER_SETTING_KEYS:
        provider = normalize_llm_provider(payload.value)
        if provider != payload.value.strip().lower():
            raise HTTPException(
                status_code=422,
                detail="Provider must be one of: anthropic, bedrock, openai",
            )
        payload.value = provider
    elif key in MODEL_SETTING_KEYS:
        payload.value = payload.value.strip()
        if not payload.value:
            raise HTTPException(status_code=422, detail="Model cannot be blank")
    elif key in _BOOLEAN_SETTING_KEYS:
        normalized = payload.value.strip().lower()
        if normalized not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
            raise HTTPException(status_code=422, detail="Value must be a boolean toggle")
        payload.value = "true" if normalized in {"true", "1", "yes", "on"} else "false"
    elif key in _INTEGER_SETTING_KEYS:
        try:
            value = int(payload.value)
        except ValueError:
            raise HTTPException(status_code=422, detail="Value must be an integer")
        if value < 1:
            raise HTTPException(status_code=422, detail="Value must be at least 1")
        payload.value = str(value)
    elif key in SECRET_SETTING_KEYS:
        payload.value = payload.value.strip()
    elif key.startswith("skill_"):
        payload.value = payload.value.strip()

    await db.execute(
        update(SystemSetting)
        .where(SystemSetting.key == key)
        .values(value=payload.value, updated_at=func.now())
    )
    result = await db.execute(
        select(
            SystemSetting.key,
            SystemSetting.value,
            SystemSetting.description,
            SystemSetting.updated_at,
        ).where(SystemSetting.key == key)
    )
    setting_key, value, description, updated_at = result.one()
    return SettingResponse(
        key=setting_key,
        value=mask_setting_value(setting_key, value or ""),
        description=description,
        updated_at=updated_at,
    )
