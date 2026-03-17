"""Schemas for system settings API."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class SettingResponse(BaseModel):
    key: str
    value: str
    description: Optional[str] = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class SettingUpdate(BaseModel):
    value: str
