"""Evolution API — bridge between the engine, the UI, and external systems.

Engine-facing endpoints (control-plane):
  POST /events              — engine reports evolution lifecycle events
  PUT  /events/{request_id} — engine updates an evolution's final status
  GET  /inceptions?status=  — engine polls for pending inceptions
  PUT  /inceptions/{id}     — engine marks inception as applied/rejected
  POST /purpose             — engine stores a purpose version
  GET  /backlog             — engine fetches the persisted proactive backlog
  POST /backlog/sync        — engine replaces or updates the proactive backlog
  PUT  /backlog/{id}        — engine updates task execution state
  POST /notifications       — engine emits or refreshes a persistent blocker notification

UI-facing endpoints (operational-plane):
  GET  /events              — evolution history (paginated)
  GET  /events/{request_id} — single evolution detail
  GET  /inceptions          — inception history
  POST /inceptions          — submit a new inception
  GET  /purpose             — current purpose
  GET  /purpose/history     — all purpose versions
  GET  /backlog             — proactive roadmap with task status
  GET  /notifications       — active or historical system notifications
  PUT  /notifications/{id}/acknowledge — dismiss a notification from the list
  GET  /status              — dashboard summary
"""

import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.config import settings
from app.database import get_db
from app.models.admin import AdminUser
from app.models.evolution import (
    EvolutionBacklogItemRecord,
    EvolutionEventRecord,
    InceptionRecord,
    PurposeRecord,
    SystemNotificationRecord,
)
from app.schemas.evolution import (
    BacklogItemResponse,
    BacklogItemUpdate,
    BacklogSyncRequest,
    DashboardStatusResponse,
    EvolutionEventCreate,
    EvolutionEventResponse,
    InceptionCreate,
    InceptionResponse,
    InceptionUpdate,
    PurposeCreate,
    PurposeResponse,
    SystemNotificationCreate,
    SystemNotificationResponse,
)

router = APIRouter(prefix="/evolution", tags=["evolution"])
logger = logging.getLogger(__name__)

# In-memory flag for on-demand analysis trigger (cleared after engine polls it)
_analysis_trigger_flag = False
_NOTIFICATION_DEDUP_WINDOW = timedelta(hours=24)
_SEVERITY_ORDER = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}
_PLAIN_TEXT_PURPOSE_NAME = "User-Defined Purpose"


def _notification_message_hash(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def _normalize_inline_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _derive_purpose_name(purpose_text: str) -> str:
    for raw_line in purpose_text.splitlines():
        candidate = _normalize_inline_text(raw_line.lstrip("-*•").strip())
        if not candidate:
            continue
        first_sentence = re.split(r"(?<=[.!?])\s+", candidate, maxsplit=1)[0].strip(" .,:;-")
        if 3 <= len(first_sentence) <= 80:
            return first_sentence
        if 3 <= len(candidate) <= 80:
            return candidate
        break
    return _PLAIN_TEXT_PURPOSE_NAME


def _serialize_purpose_yaml(
    *,
    version: int,
    name: str,
    description: str,
    functional_requirements: list[str] | None = None,
    technical_requirements: list[str] | None = None,
    security_requirements: list[str] | None = None,
    constraints: list[str] | None = None,
    evolution_directives: list[str] | None = None,
) -> str:
    def yaml_scalar(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    def append_yaml_list(lines: list[str], key: str, values: list[str]) -> None:
        if not values:
            lines.append(f"{key}: []")
            return
        lines.append(f"{key}:")
        for item in values:
            lines.append(f"  - {yaml_scalar(item)}")

    lines = [
        f"version: {version}",
        f'updated_at: "{datetime.now(UTC).isoformat()}"',
        "",
        "identity:",
        f"  name: {yaml_scalar(name)}",
        f"  description: {yaml_scalar(description)}",
        "",
    ]
    append_yaml_list(lines, "functional_requirements", functional_requirements or [])
    lines.append("")
    append_yaml_list(lines, "technical_requirements", technical_requirements or [])
    lines.append("")
    append_yaml_list(lines, "security_requirements", security_requirements or [])
    lines.append("")
    append_yaml_list(lines, "constraints", constraints or [])
    lines.append("")
    append_yaml_list(lines, "evolution_directives", evolution_directives or [])
    return "\n".join(lines).rstrip() + "\n"


def _build_purpose_yaml_from_text(purpose_text: str, version: int) -> str:
    bullet_requirements: list[str] = []
    normalized_lines: list[str] = []

    for raw_line in purpose_text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        bullet_match = re.match(r"^[-*•]\s+(.*)$", stripped)
        cleaned = _normalize_inline_text(bullet_match.group(1) if bullet_match else stripped)
        if not cleaned:
            continue
        normalized_lines.append(cleaned)
        if bullet_match:
            bullet_requirements.append(cleaned)

    if not normalized_lines:
        raise ValueError("Purpose text cannot be empty.")

    description = " ".join(normalized_lines)
    return _serialize_purpose_yaml(
        version=version,
        name=_derive_purpose_name(purpose_text),
        description=description,
        functional_requirements=bullet_requirements,
    )


def _should_forward_notification(severity: str) -> bool:
    """Return whether a notification should trigger an external alert."""
    webhook_url = settings.notification_webhook_url.strip()
    if not webhook_url:
        return False

    threshold = _SEVERITY_ORDER.get(settings.notification_webhook_min_severity.strip().lower(), 3)
    current = _SEVERITY_ORDER.get((severity or "").strip().lower(), 0)
    return current >= threshold


def _notification_webhook_payload(record: SystemNotificationRecord) -> dict:
    return {
        "id": record.id,
        "source": record.source,
        "kind": record.kind,
        "severity": record.severity,
        "message": record.message,
        "acknowledged": record.acknowledged,
        "update_count": record.update_count,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


async def _deliver_external_notification(payload: dict) -> None:
    """Best-effort delivery of critical notifications to an external webhook."""
    webhook_url = settings.notification_webhook_url.strip()
    if not webhook_url:
        return

    headers = {"Content-Type": "application/json"}
    if settings.notification_webhook_bearer_token.strip():
        headers["Authorization"] = f"Bearer {settings.notification_webhook_bearer_token.strip()}"

    timeout_seconds = max(1.0, float(settings.notification_webhook_timeout_seconds))
    timeout = httpx.Timeout(timeout_seconds, connect=min(2.0, timeout_seconds))

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(webhook_url, json=payload, headers=headers)
            response.raise_for_status()
    except Exception as exc:
        logger.warning("notifications.webhook_failed", extra={"error": str(exc)})


def _schedule_external_notification(
    background_tasks: BackgroundTasks | None,
    record: SystemNotificationRecord,
) -> None:
    """Queue external alert delivery when configured and severe enough."""
    if background_tasks is None or not _should_forward_notification(record.severity):
        return
    background_tasks.add_task(
        _deliver_external_notification,
        _notification_webhook_payload(record),
    )


async def _create_or_update_notification(
    payload: SystemNotificationCreate,
    db: AsyncSession,
) -> SystemNotificationRecord:
    """Merge repeated notifications with the same exact text inside the last 24 hours."""
    now = datetime.now(UTC)
    message_hash = _notification_message_hash(payload.message)

    result = await db.execute(
        select(SystemNotificationRecord)
        .where(SystemNotificationRecord.message_hash == message_hash)
        .where(SystemNotificationRecord.message == payload.message)
        .where(SystemNotificationRecord.updated_at >= now - _NOTIFICATION_DEDUP_WINDOW)
        .order_by(desc(SystemNotificationRecord.updated_at))
        .limit(1)
    )
    record = result.scalar_one_or_none()

    if record is None:
        record = SystemNotificationRecord(
            source=payload.source,
            kind=payload.kind,
            severity=payload.severity,
            message=payload.message,
            message_hash=message_hash,
            acknowledged=False,
            update_count=0,
        )
        db.add(record)
        await db.flush()
        return record

    record.source = payload.source
    record.kind = payload.kind
    record.severity = payload.severity
    record.updated_at = now
    record.update_count += 1
    record.acknowledged = False
    record.acknowledged_at = None
    await db.flush()
    return record


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


@router.get("/events", response_model=list[EvolutionEventResponse])
async def list_evolution_events(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[EvolutionEventRecord]:
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


@router.get("/inceptions", response_model=list[InceptionResponse])
async def list_inceptions(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[InceptionRecord]:
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
        record.processed_at = datetime.now(UTC)

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
    """Engine stores a new purpose version.

    Startup can legitimately re-post the current Purpose. Treat version as an
    idempotency key so engine restarts do not raise a 500 on duplicate inserts.
    """
    try:
        content_yaml = payload.content_yaml or _build_purpose_yaml_from_text(
            payload.purpose_text or "",
            payload.version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = await db.execute(
        select(PurposeRecord).where(PurposeRecord.version == payload.version)
    )
    record = result.scalar_one_or_none()

    if record:
        record.content_yaml = content_yaml
        record.inception_id = payload.inception_id
    else:
        record = PurposeRecord(
            version=payload.version,
            content_yaml=content_yaml,
            inception_id=payload.inception_id,
        )
        db.add(record)

    await db.flush()
    return record


@router.get("/purpose", response_model=PurposeResponse | None)
async def get_current_purpose(
    db: AsyncSession = Depends(get_db),
) -> PurposeRecord | None:
    """Get the current (highest version) purpose."""
    result = await db.execute(
        select(PurposeRecord).order_by(desc(PurposeRecord.version)).limit(1)
    )
    return result.scalar_one_or_none()


@router.get("/purpose/history", response_model=list[PurposeResponse])
async def list_purpose_history(
    db: AsyncSession = Depends(get_db),
) -> list[PurposeRecord]:
    """List all purpose versions, newest first."""
    result = await db.execute(
        select(PurposeRecord).order_by(desc(PurposeRecord.version))
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Backlog
# ---------------------------------------------------------------------------


@router.get("/backlog", response_model=list[BacklogItemResponse])
async def list_backlog_items(
    purpose_version: int | None = Query(default=None),
    include_completed: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> list[EvolutionBacklogItemRecord]:
    """List proactive backlog items, ordered by plan sequence."""
    query = select(EvolutionBacklogItemRecord).order_by(
        EvolutionBacklogItemRecord.purpose_version.desc(),
        EvolutionBacklogItemRecord.sequence.asc(),
        EvolutionBacklogItemRecord.created_at.asc(),
    )
    if purpose_version is not None:
        query = query.where(EvolutionBacklogItemRecord.purpose_version == purpose_version)
    if not include_completed:
        query = query.where(
            EvolutionBacklogItemRecord.status.notin_(["done", "abandoned"])
        )
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("/backlog/sync", response_model=list[BacklogItemResponse])
async def sync_backlog(
    payload: BacklogSyncRequest,
    db: AsyncSession = Depends(get_db),
) -> list[EvolutionBacklogItemRecord]:
    """Replace or update the proactive backlog for a Purpose version.

    The planner sends the full desired roadmap for the active Purpose version.
    Existing non-terminal items omitted from the new plan are marked abandoned.
    Terminal items stay untouched so progress survives replans.
    """
    result = await db.execute(
        select(EvolutionBacklogItemRecord).where(
            EvolutionBacklogItemRecord.purpose_version == payload.purpose_version
        )
    )
    existing_records = list(result.scalars().all())
    existing_by_key = {record.task_key: record for record in existing_records}
    seen_keys: set[str] = set()

    for item in payload.items:
        seen_keys.add(item.task_key)
        record = existing_by_key.get(item.task_key)
        item_data = item.model_dump()
        app_spec = item_data.pop("app_spec", None)
        if app_spec is not None:
            item_data["app_spec"] = app_spec

        if record:
            preserve_blocked_state = record.status == "blocked" and item.status == "pending"
            for field, value in item_data.items():
                if field == "status" and record.status in {"done", "abandoned"}:
                    continue
                if field == "status" and record.status == "in_progress" and value == "pending":
                    continue
                if field == "status" and preserve_blocked_state:
                    continue
                setattr(record, field, value)
            if not preserve_blocked_state or item.blocked_reason:
                record.blocked_reason = item.blocked_reason
            if record.status in {"done", "abandoned"} and not record.completed_at:
                record.completed_at = datetime.now(UTC)
            continue

        record = EvolutionBacklogItemRecord(
            purpose_version=payload.purpose_version,
            **item_data,
        )
        if record.status in {"done", "abandoned"}:
            record.completed_at = datetime.now(UTC)
        db.add(record)

    for record in existing_records:
        if record.task_key in seen_keys:
            continue
        if record.status in {"done", "abandoned"}:
            continue
        record.status = "abandoned"
        record.blocked_reason = "Removed from replanned backlog"
        record.completed_at = record.completed_at or datetime.now(UTC)

    await db.flush()

    refreshed = await db.execute(
        select(EvolutionBacklogItemRecord)
        .where(EvolutionBacklogItemRecord.purpose_version == payload.purpose_version)
        .order_by(
            EvolutionBacklogItemRecord.sequence.asc(),
            EvolutionBacklogItemRecord.created_at.asc(),
        )
    )
    return list(refreshed.scalars().all())


@router.put("/backlog/{item_id}", response_model=BacklogItemResponse)
async def update_backlog_item(
    item_id: str,
    payload: BacklogItemUpdate,
    db: AsyncSession = Depends(get_db),
) -> EvolutionBacklogItemRecord:
    """Update execution state for a proactive backlog item."""
    result = await db.execute(
        select(EvolutionBacklogItemRecord).where(EvolutionBacklogItemRecord.id == item_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Backlog item not found")

    update_data = payload.model_dump(exclude_unset=True)
    app_spec = update_data.pop("app_spec", None)
    if app_spec is not None:
        update_data["app_spec"] = app_spec

    for field, value in update_data.items():
        setattr(record, field, value)

    if record.status in {"done", "abandoned"} and not record.completed_at:
        record.completed_at = datetime.now(UTC)
    if "completed_at" not in update_data and record.status not in {"done", "abandoned"}:
        record.completed_at = None

    await db.flush()
    return record


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@router.post("/notifications", response_model=SystemNotificationResponse)
async def create_or_update_notification(
    payload: SystemNotificationCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> SystemNotificationRecord:
    """Create a new notification or refresh the recent identical one."""
    record = await _create_or_update_notification(payload, db)
    _schedule_external_notification(background_tasks, record)
    return record


@router.get("/notifications", response_model=list[SystemNotificationResponse])
async def list_notifications(
    include_acknowledged: bool = Query(default=False),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[SystemNotificationRecord]:
    """List notifications with the newest updates first."""
    query = (
        select(SystemNotificationRecord)
        .order_by(
            desc(SystemNotificationRecord.updated_at),
            desc(SystemNotificationRecord.created_at),
        )
        .limit(limit)
    )
    if not include_acknowledged:
        query = query.where(SystemNotificationRecord.acknowledged.is_(False))
    result = await db.execute(query)
    return list(result.scalars().all())


@router.put(
    "/notifications/{notification_id}/acknowledge",
    response_model=SystemNotificationResponse,
)
async def acknowledge_notification(
    notification_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin),
) -> SystemNotificationRecord:
    """Acknowledge a notification so it disappears until updated again."""
    result = await db.execute(
        select(SystemNotificationRecord).where(SystemNotificationRecord.id == notification_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Notification not found")

    record.acknowledged = True
    record.acknowledged_at = datetime.now(UTC)
    await db.flush()
    return record


# ---------------------------------------------------------------------------
# Dashboard Status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=DashboardStatusResponse)
async def dashboard_status(
    db: AsyncSession = Depends(get_db),
) -> DashboardStatusResponse:
    """Aggregated dashboard data for the Evolution Monitor UI."""
    # Old terminal reports can be lost during backend restarts. Treat only
    # recent non-terminal rows as "active" so stale historical entries do not
    # make the UI look permanently busy.
    active_cutoff = datetime.now(UTC) - timedelta(minutes=15)

    # Count evolutions by status
    total = await db.execute(select(func.count()).select_from(EvolutionEventRecord))
    total_count = total.scalar() or 0

    active = await db.execute(
        select(func.count())
        .select_from(EvolutionEventRecord)
        .where(EvolutionEventRecord.status.notin_(["completed", "failed"]))
        .where(EvolutionEventRecord.created_at >= active_cutoff)
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

    active_notifications = await db.execute(
        select(func.count())
        .select_from(SystemNotificationRecord)
        .where(SystemNotificationRecord.acknowledged.is_(False))
    )
    active_notification_count = active_notifications.scalar() or 0

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
        active_notifications=active_notification_count,
        last_evolution=EvolutionEventResponse.model_validate(last_record) if last_record else None,
    )


# ---------------------------------------------------------------------------
# Analysis Trigger (on-demand proactive analysis)
# ---------------------------------------------------------------------------


@router.post("/trigger-analysis", status_code=202)
async def trigger_analysis(
    _admin: AdminUser = Depends(get_current_admin),
) -> dict:
    """Admin triggers an immediate proactive analysis cycle.

    Sets a flag that the engine polls. The engine will run the analysis
    on its next iteration regardless of the 60-minute throttle.
    """
    global _analysis_trigger_flag
    _analysis_trigger_flag = True
    return {"triggered": True, "message": "Proactive analysis will run on next engine poll."}


@router.get("/trigger-analysis")
async def check_analysis_trigger() -> dict:
    """Engine polls this to check if an on-demand analysis was requested.

    The flag is cleared after being read (consume-once semantics).
    """
    global _analysis_trigger_flag
    triggered = _analysis_trigger_flag
    if triggered:
        _analysis_trigger_flag = False
    return {"triggered": triggered}
