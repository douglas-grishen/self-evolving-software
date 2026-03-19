"""Chat API — AI assistant with full system context.

Provides a conversational interface so users can ask anything about
the self-evolving system: its Purpose, built apps, evolution history,
what exists, what failed, how it works, etc.

Uses the Anthropic API via direct httpx calls (no SDK dependency).
Streams the response using Server-Sent Events (SSE).
"""

from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.apps import AppRecord
from app.models.evolution import EvolutionEventRecord, InceptionRecord, PurposeRecord
from app.models.system_settings import SystemSetting

router = APIRouter(prefix="/chat", tags=["chat"])

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_MODEL = "claude-sonnet-4-5-20250929"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

async def _build_system_prompt(db: AsyncSession) -> str:
    """Fetch live data from DB and build a comprehensive system prompt."""

    # ── Purpose ──────────────────────────────────────────────────────────────
    purpose_result = await db.execute(
        select(PurposeRecord).order_by(desc(PurposeRecord.created_at)).limit(1)
    )
    purpose = purpose_result.scalar_one_or_none()
    purpose_section = purpose.content_yaml if purpose else "(No Purpose defined yet)"

    # ── Apps ─────────────────────────────────────────────────────────────────
    apps_result = await db.execute(select(AppRecord).order_by(AppRecord.created_at))
    apps = apps_result.scalars().all()

    if apps:
        app_lines = []
        for app in apps:
            features = [f"  - {f.name}: {f.description}" for f in app.features] if app.features else ["  (no features yet)"]
            caps = [f"  - {c.name} ({'background' if c.is_background else 'interactive'})" for c in app.capabilities] if app.capabilities else []
            app_lines.append(
                f"• {app.icon} {app.name} [{app.status}]\n"
                f"  Goal: {app.goal}\n"
                f"  Features:\n" + "\n".join(features) +
                (("\n  Capabilities:\n" + "\n".join(caps)) if caps else "")
            )
        apps_section = "\n\n".join(app_lines)
    else:
        apps_section = "(No apps built yet)"

    # ── Evolution history ─────────────────────────────────────────────────────
    evts_result = await db.execute(
        select(EvolutionEventRecord)
        .order_by(desc(EvolutionEventRecord.created_at))
        .limit(30)
    )
    evts = evts_result.scalars().all()

    if evts:
        evt_lines = []
        for e in evts:
            status_icon = {"completed": "✅", "failed": "❌", "received": "⏳"}.get(e.status, "🔄")
            summary = e.plan_summary or e.user_request[:120].replace("\n", " ")
            files = ""
            if e.events_json and isinstance(e.events_json, dict):
                changes = e.events_json.get("plan_changes", [])
                if changes:
                    files = " → " + ", ".join(f"{c['action']} {c['file_path']}" for c in changes[:5])
                    if len(changes) > 5:
                        files += f" (+{len(changes)-5} more)"
            evt_lines.append(f"{status_icon} {summary}{files}")
        evolutions_section = "\n".join(evt_lines)
    else:
        evolutions_section = "(No evolution history yet)"

    # ── Inceptions ───────────────────────────────────────────────────────────
    inc_result = await db.execute(
        select(InceptionRecord)
        .order_by(desc(InceptionRecord.submitted_at))
        .limit(10)
    )
    inceptions = inc_result.scalars().all()

    if inceptions:
        inc_lines = [
            f"• [{i.status}] {i.directive[:150]}"
            for i in inceptions
        ]
        inceptions_section = "\n".join(inc_lines)
    else:
        inceptions_section = "(No inceptions yet)"

    return f"""You are the AI assistant for a Self-Evolving Software system. \
Your role is to help the user understand everything about this system — \
its Purpose, architecture, current apps, evolution history, what works, what failed, and how it all fits together.

Be conversational, clear, and precise. Use the live data below to give accurate answers. \
When asked about something not in the data, be honest. \
Answer in the same language the user uses (Spanish or English).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SYSTEM PURPOSE (what this software is meant to become)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{purpose_section}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
APPS CURRENTLY BUILT ({len(apps)} total)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{apps_section}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EVOLUTION HISTORY (last {len(evts)} cycles — what the engine has done)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{evolutions_section}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INCEPTIONS (directives to modify the Purpose)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{inceptions_section}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARCHITECTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Engine (MAPE-K loop): runs every 60 min, monitors anomalies, analyzes Purpose gaps, plans changes,
  generates code with Claude Sonnet, validates (pytest + docker build), deploys if passing.
• Backend API (FastAPI + PostgreSQL): stores apps, evolution events, inceptions, system settings.
• Frontend (React): macOS-style desktop UI showing app icons, menu bar, system windows.
• The engine writes code to /opt/evolved-app/, builds Docker images, and restarts containers.
• Apps go through: planned → building → active lifecycle.
"""


# ---------------------------------------------------------------------------
# API key helper
# ---------------------------------------------------------------------------

async def _get_api_key(db: AsyncSession) -> str:
    """Get Anthropic API key: prefer system_settings, fall back to env var."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "anthropic_api_key")
    )
    setting = result.scalar_one_or_none()
    if setting and setting.value:
        return setting.value
    return os.environ.get("ENGINE_ANTHROPIC_API_KEY", "")


# ---------------------------------------------------------------------------
# Streaming generator
# ---------------------------------------------------------------------------

async def _stream_anthropic(api_key: str, system: str, messages: list[dict]) -> AsyncIterator[str]:
    """Call Anthropic API with streaming and yield SSE chunks."""
    payload = {
        "model": _MODEL,
        "max_tokens": 2048,
        "system": system,
        "messages": messages,
        "stream": True,
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", _ANTHROPIC_URL, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str:
                    continue
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "content_block_delta":
                    text = data.get("delta", {}).get("text", "")
                    if text:
                        # Yield as SSE
                        yield f"data: {json.dumps({'text': text})}\n\n"
                elif data.get("type") == "message_stop":
                    yield "data: [DONE]\n\n"
                    return


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("")
async def chat(
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream a chat response with full system context."""
    system = await _build_system_prompt(db)
    api_key = await _get_api_key(db)

    if not api_key:
        async def _no_key():
            yield 'data: {"text": "⚠️ No Anthropic API key configured. Set it in Settings → Anthropic API Key."}\n\n'
            yield "data: [DONE]\n\n"
        return StreamingResponse(_no_key(), media_type="text/event-stream")

    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    return StreamingResponse(
        _stream_anthropic(api_key, system, messages),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering for SSE
        },
    )
