"""Chat API — AI assistant with full system context.

Provides a conversational interface so users can ask anything about
the self-evolving system: its Purpose, built apps, evolution history,
what exists, what failed, and how it all fits together.

Provider strategy:
  1. Prefer Anthropic when a key is configured in system settings
  2. Fall back to Amazon Bedrock when Anthropic is unavailable or exhausted
  3. Return an explicit SSE error message if both providers fail
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncIterator

import boto3
import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.apps import AppRecord
from app.models.evolution import EvolutionEventRecord, InceptionRecord, PurposeRecord
from app.models.system_settings import SystemSetting

router = APIRouter(prefix="/chat", tags=["chat"])

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_MODEL = os.environ.get("ENGINE_ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
_BEDROCK_REGION = os.environ.get("ENGINE_BEDROCK_REGION") or os.environ.get(
    "AWS_REGION",
    "us-east-1",
)
_BEDROCK_MODEL_ID = os.environ.get(
    "ENGINE_BEDROCK_MODEL_ID",
    "anthropic.claude-sonnet-4-20250514-v1:0",
)


class ChatProviderError(RuntimeError):
    """Raised when an upstream chat provider cannot serve the request."""


def _sse_text(text: str) -> str:
    return f"data: {json.dumps({'text': text})}\n\n"


def _sse_done() -> str:
    return "data: [DONE]\n\n"


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
Always answer in English.

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
# Provider helpers
# ---------------------------------------------------------------------------

def _extract_anthropic_error_message(payload: bytes) -> str:
    """Convert Anthropic error payloads into user-facing messages."""
    if not payload:
        return "Anthropic returned an empty error response."
    try:
        parsed = json.loads(payload.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return payload.decode("utf-8", "replace")[:500]

    error = parsed.get("error", {})
    return error.get("message") or parsed.get("message") or "Anthropic returned an unknown error."


def _to_bedrock_messages(messages: list[dict]) -> list[dict]:
    """Translate chat messages into Bedrock Converse message blocks."""
    return [
        {
            "role": message["role"],
            "content": [{"text": message["content"]}],
        }
        for message in messages
    ]


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
            if resp.is_error:
                error_payload = await resp.aread()
                raise ChatProviderError(_extract_anthropic_error_message(error_payload))

            emitted_text = False
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
                        emitted_text = True
                        yield _sse_text(text)
                elif data.get("type") == "message_stop":
                    yield _sse_done()
                    return

            if not emitted_text:
                raise ChatProviderError("Anthropic returned no text.")


async def _stream_bedrock(system: str, messages: list[dict]) -> AsyncIterator[str]:
    """Call Bedrock Converse API and return a single SSE text chunk."""
    client = boto3.client("bedrock-runtime", region_name=_BEDROCK_REGION)

    try:
        response = await asyncio.to_thread(
            client.converse,
            modelId=_BEDROCK_MODEL_ID,
            system=[{"text": system}],
            messages=_to_bedrock_messages(messages),
            inferenceConfig={"maxTokens": 2048},
        )
    except Exception as exc:  # pragma: no cover - exact boto error depends on runtime
        raise ChatProviderError(str(exc)) from exc

    content = response.get("output", {}).get("message", {}).get("content", [])
    text = "".join(block.get("text", "") for block in content if isinstance(block, dict))
    if not text.strip():
        raise ChatProviderError("Bedrock returned no text.")

    yield _sse_text(text)
    yield _sse_done()


async def _stream_chat_response(
    *,
    system: str,
    messages: list[dict],
    anthropic_api_key: str,
) -> AsyncIterator[str]:
    """Try Anthropic first, then fall back to Bedrock, surfacing real errors."""
    provider_errors: list[str] = []

    if anthropic_api_key:
        try:
            async for chunk in _stream_anthropic(anthropic_api_key, system, messages):
                yield chunk
            return
        except ChatProviderError as exc:
            provider_errors.append(f"Anthropic: {exc}")

    try:
        async for chunk in _stream_bedrock(system, messages):
            yield chunk
        return
    except ChatProviderError as exc:
        provider_errors.append(f"Bedrock: {exc}")

    combined = " ".join(provider_errors) or "No chat provider is configured."
    yield _sse_text(f"⚠️ Chat is unavailable right now. {combined}")
    yield _sse_done()


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
    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    return StreamingResponse(
        _stream_chat_response(
            system=system,
            messages=messages,
            anthropic_api_key=api_key,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering for SSE
        },
    )
