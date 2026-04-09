"""Apps, Features, and Capabilities API.

Engine-facing endpoints:
  POST /apps                — engine creates a new app (with features & capabilities)
  PUT  /apps/{id}           — engine updates app status
  POST /apps/{id}/features  — engine adds a feature to an app
  POST /capabilities        — engine creates a standalone capability
  PUT  /features/{id}       — engine updates feature status
  PUT  /capabilities/{id}   — engine updates capability status

UI-facing endpoints:
  GET  /apps                — list all apps (for desktop icons)
  GET  /apps/{id}           — get full app detail with features & capabilities
  GET  /capabilities        — list all capabilities
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.apps import (
    AppRecord,
    CapabilityRecord,
    FeatureRecord,
    app_capabilities,
    feature_capabilities,
)
from app.schemas.apps import (
    AppBrief,
    AppCreate,
    AppResponse,
    AppUpdate,
    CapabilityCreate,
    CapabilityResponse,
    FeatureCreate,
    FeatureResponse,
)

router = APIRouter(prefix="/apps", tags=["apps"])
logger = logging.getLogger(__name__)

_APP_STATUSES = {"planned", "building", "active", "archived"}


# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------


@router.get("", response_model=list[AppBrief])
async def list_apps(
    status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list:
    """List all apps for the desktop. Returns brief info for icon display."""
    query = (
        select(AppRecord)
        .options(
            selectinload(AppRecord.features),
            selectinload(AppRecord.capabilities),
        )
        .order_by(desc(AppRecord.created_at))
    )
    if status:
        query = query.where(AppRecord.status == status)
    result = await db.execute(query)
    apps = list(result.scalars().all())

    return [
        AppBrief(
            id=app.id,
            name=app.name,
            icon=app.icon,
            status=app.status,
            goal=app.goal,
            feature_count=len(app.features),
            capability_count=len(app.capabilities),
        )
        for app in apps
    ]


@router.get("/{app_id}", response_model=AppResponse)
async def get_app(
    app_id: str,
    db: AsyncSession = Depends(get_db),
) -> AppRecord:
    """Get full app detail with features and capabilities."""
    return await _load_app_record(db, app_id)


@router.post("", response_model=AppResponse, status_code=201)
async def create_app(
    payload: AppCreate,
    db: AsyncSession = Depends(get_db),
) -> AppRecord:
    """Create a new app with optional features and capabilities.

    Can be called by the engine (no auth) or admin (with auth).
    """
    _validate_app_status(payload.status)
    existing = await _load_app_record_by_name(db, payload.name)
    if existing is not None:
        logger.warning("apps.create.duplicate_name name=%s", payload.name)
        raise HTTPException(
            status_code=409,
            detail=f"App '{payload.name}' already exists",
        )

    # Create app record
    app = AppRecord(
        name=payload.name,
        description=payload.description,
        icon=payload.icon or _default_icon(payload.name),
        goal=payload.goal,
        status=payload.status,
        metadata_json=payload.metadata_json,
    )
    db.add(app)
    try:
        await db.flush()  # get app.id
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "apps.create.integrity_error name=%s error_type=%s error=%s",
            payload.name,
            type(exc).__name__,
            str(exc),
        )
        existing = await _load_app_record_by_name(db, payload.name)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"App '{payload.name}' already exists",
            ) from exc
        raise HTTPException(
            status_code=500,
            detail="Failed to create app",
        ) from exc

    # Link standalone capabilities
    if payload.capability_ids:
        result = await db.execute(
            select(CapabilityRecord).where(CapabilityRecord.id.in_(payload.capability_ids))
        )
        for cap in result.scalars().all():
            await db.execute(
                insert(app_capabilities).values(app_id=app.id, capability_id=cap.id)
            )

    # Create features
    for feat_data in payload.features:
        feature = FeatureRecord(
            app_id=app.id,
            name=feat_data.name,
            description=feat_data.description,
            user_facing_description=feat_data.user_facing_description,
        )
        db.add(feature)
        await db.flush()

        # Link capabilities to feature
        if feat_data.capability_ids:
            result = await db.execute(
                select(CapabilityRecord).where(CapabilityRecord.id.in_(feat_data.capability_ids))
            )
            for cap in result.scalars().all():
                await db.execute(
                    insert(feature_capabilities).values(
                        feature_id=feature.id,
                        capability_id=cap.id,
                    )
                )

    await db.flush()
    return await _load_app_record(db, app.id)


@router.put("/{app_id}", response_model=AppResponse)
async def update_app(
    app_id: str,
    payload: AppUpdate,
    db: AsyncSession = Depends(get_db),
) -> AppRecord:
    """Update an app's metadata or status."""
    result = await db.execute(
        select(AppRecord).where(AppRecord.id == app_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="App not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(record, field, value)

    await db.flush()
    return record


@router.post("/{app_id}/features", response_model=FeatureResponse, status_code=201)
async def add_feature(
    app_id: str,
    payload: FeatureCreate,
    db: AsyncSession = Depends(get_db),
) -> FeatureRecord:
    """Add a feature to an existing app."""
    # Verify app exists
    result = await db.execute(select(AppRecord).where(AppRecord.id == app_id))
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="App not found")

    feature = FeatureRecord(
        app_id=app_id,
        name=payload.name,
        description=payload.description,
        user_facing_description=payload.user_facing_description,
    )
    db.add(feature)
    await db.flush()

    # Link capabilities
    if payload.capability_ids:
        result = await db.execute(
            select(CapabilityRecord).where(CapabilityRecord.id.in_(payload.capability_ids))
        )
        for cap in result.scalars().all():
            await db.execute(
                insert(feature_capabilities).values(
                    feature_id=feature.id,
                    capability_id=cap.id,
                )
            )

    await db.flush()
    return await _load_feature_record(db, feature.id)


@router.put("/features/{feature_id}", response_model=FeatureResponse)
async def update_feature(
    feature_id: str,
    status: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> FeatureRecord:
    """Update a feature's status (e.g., planned → building → implemented)."""
    result = await db.execute(
        select(FeatureRecord).where(FeatureRecord.id == feature_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Feature not found")
    record.status = status
    await db.flush()
    return record


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@router.get("/capabilities/all", response_model=list[CapabilityResponse])
async def list_capabilities(
    status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[CapabilityRecord]:
    """List all capabilities."""
    query = select(CapabilityRecord).order_by(desc(CapabilityRecord.created_at))
    if status:
        query = query.where(CapabilityRecord.status == status)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("/capabilities", response_model=CapabilityResponse, status_code=201)
async def create_capability(
    payload: CapabilityCreate,
    db: AsyncSession = Depends(get_db),
) -> CapabilityRecord:
    """Create a standalone capability."""
    cap = CapabilityRecord(
        name=payload.name,
        description=payload.description,
        is_background=payload.is_background,
    )
    db.add(cap)
    await db.flush()
    return cap


@router.put("/capabilities/{capability_id}", response_model=CapabilityResponse)
async def update_capability(
    capability_id: str,
    status: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> CapabilityRecord:
    """Update a capability's status."""
    result = await db.execute(
        select(CapabilityRecord).where(CapabilityRecord.id == capability_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Capability not found")
    record.status = status
    await db.flush()
    return record


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_icon(name: str) -> str:
    """Generate a default emoji icon based on app name keywords."""
    name_lower = name.lower()
    icon_map = {
        "chat": "\U0001f4ac",       # speech bubble
        "message": "\U0001f4ac",
        "email": "\U0001f4e7",
        "mail": "\U0001f4e7",
        "calendar": "\U0001f4c5",
        "schedule": "\U0001f4c5",
        "note": "\U0001f4dd",
        "todo": "\u2705",           # check mark
        "task": "\u2705",
        "photo": "\U0001f4f7",
        "image": "\U0001f5bc\ufe0f",
        "music": "\U0001f3b5",
        "video": "\U0001f3ac",
        "map": "\U0001f5fa\ufe0f",
        "weather": "\u2600\ufe0f",
        "shop": "\U0001f6d2",
        "store": "\U0001f6d2",
        "finance": "\U0001f4b0",
        "money": "\U0001f4b0",
        "health": "\U0001f3e5",
        "fitness": "\U0001f3cb\ufe0f",
        "game": "\U0001f3ae",
        "code": "\U0001f4bb",
        "dev": "\U0001f4bb",
        "data": "\U0001f4ca",
        "analytics": "\U0001f4ca",
        "settings": "\u2699\ufe0f",
        "security": "\U0001f512",
        "auth": "\U0001f512",
    }
    for keyword, icon in icon_map.items():
        if keyword in name_lower:
            return icon
    return "\U0001f4e6"  # default: package emoji


async def _load_app_record(db: AsyncSession, app_id: str) -> AppRecord:
    """Load an app with nested relationships eagerly populated for API responses."""
    result = await db.execute(
        select(AppRecord)
        .options(
            selectinload(AppRecord.features).selectinload(FeatureRecord.capabilities),
            selectinload(AppRecord.capabilities),
        )
        .where(AppRecord.id == app_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="App not found")
    return record


async def _load_feature_record(db: AsyncSession, feature_id: str) -> FeatureRecord:
    """Load a feature with capabilities eagerly populated for API responses."""
    result = await db.execute(
        select(FeatureRecord)
        .options(selectinload(FeatureRecord.capabilities))
        .where(FeatureRecord.id == feature_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Feature not found")
    return record


async def _load_app_record_by_name(
    db: AsyncSession,
    name: str,
) -> AppRecord | None:
    """Return an eagerly loaded app when the name already exists."""
    result = await db.execute(
        select(AppRecord)
        .options(
            selectinload(AppRecord.features).selectinload(FeatureRecord.capabilities),
            selectinload(AppRecord.capabilities),
        )
        .where(AppRecord.name == name)
    )
    return result.scalar_one_or_none()


def _validate_app_status(status: str) -> None:
    """Reject invalid status values with a clear client-facing error."""
    if status not in _APP_STATUSES:
        logger.warning("apps.create.invalid_status status=%s", status)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid app status '{status}'",
        )
