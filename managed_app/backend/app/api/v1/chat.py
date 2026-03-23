"""Chat API — AI assistant with full system context.

Provides a conversational interface so users can ask anything about
the self-evolving system: its Purpose, built apps, evolution history,
what exists, what failed, and how it all fits together.

Provider strategy:
  1. Prefer the provider selected in system settings
  2. Fall back to other configured providers if the preferred one fails
  3. Return a deterministic local answer if all providers are unavailable
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter
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
from app.models.evolution import (
    EvolutionBacklogItemRecord,
    EvolutionEventRecord,
    InceptionRecord,
    PurposeRecord,
)
from app.models.system_settings import SystemSetting
from app.system_settings import default_model_for_provider, normalize_llm_provider

router = APIRouter(prefix="/chat", tags=["chat"])

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_MODEL = os.environ.get("ENGINE_ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
_BEDROCK_REGION = os.environ.get("ENGINE_BEDROCK_REGION") or os.environ.get(
    "AWS_REGION",
    "us-east-1",
)
_BEDROCK_MODEL_ID = os.environ.get(
    "ENGINE_BEDROCK_MODEL_ID",
    "global.anthropic.claude-sonnet-4-20250514-v1:0",
)
_OPENAI_MODEL = os.environ.get("ENGINE_OPENAI_MODEL", "gpt-5.2")
_LLM_SETTING_KEYS = ("llm_provider", "llm_model", "anthropic_api_key", "openai_api_key")


def _provider_order(
    preferred_provider: str,
    *,
    anthropic_api_key: str,
    openai_api_key: str,
) -> list[str]:
    """Return provider order honoring runtime preference and configured credentials."""
    preferred = normalize_llm_provider(
        preferred_provider or os.environ.get("ENGINE_LLM_PROVIDER")
    )
    ordered = [preferred, "anthropic", "openai", "bedrock"]
    result: list[str] = []
    for provider in ordered:
        if provider == "anthropic" and not anthropic_api_key:
            continue
        if provider == "openai" and not openai_api_key:
            continue
        if provider not in result:
            result.append(provider)
    return result


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
  generates code with the configured LLM provider, validates (pytest + docker build), deploys if passing.
• Backend API (FastAPI + PostgreSQL): stores apps, evolution events, inceptions, system settings.
• Frontend (React): macOS-style desktop UI showing app icons, menu bar, system windows.
• The engine writes code to /opt/evolved-app/, builds Docker images, and restarts containers.
• Apps go through: planned → building → active lifecycle.
"""


# ---------------------------------------------------------------------------
# API key helper
# ---------------------------------------------------------------------------

async def _get_runtime_settings(db: AsyncSession) -> dict[str, str]:
    """Fetch LLM runtime settings once so chat uses a coherent provider snapshot."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(_LLM_SETTING_KEYS))
    )
    values = {setting.key: setting.value for setting in result.scalars().all()}

    provider = normalize_llm_provider(values.get("llm_provider") or os.environ.get("ENGINE_LLM_PROVIDER"))
    model = (values.get("llm_model") or "").strip() or default_model_for_provider(provider)

    return {
        "llm_provider": provider,
        "llm_model": model,
        "anthropic_api_key": values.get("anthropic_api_key") or os.environ.get("ENGINE_ANTHROPIC_API_KEY", ""),
        "openai_api_key": values.get("openai_api_key") or os.environ.get("ENGINE_OPENAI_API_KEY", ""),
    }


def _normalize_text(text: str) -> str:
    return " ".join((text or "").split())


def _clip(text: str, limit: int = 220) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _extract_purpose_identity(content_yaml: str) -> tuple[str, str]:
    """Extract purpose name/description from YAML-ish text without extra deps."""
    name_match = re.search(r"^\s*name:\s*(.+)$", content_yaml, re.MULTILINE)
    desc_match = re.search(
        r"^\s*description:\s*>\s*\n((?:\s{4,}.+\n?)*)",
        content_yaml,
        re.MULTILINE,
    )
    name = _normalize_text(name_match.group(1)) if name_match else "Unknown purpose"
    description = ""
    if desc_match:
        description = _normalize_text(
            re.sub(r"^\s{4,}", "", desc_match.group(1), flags=re.MULTILINE)
        )
    return name, description


async def _build_local_fallback_reply(
    db: AsyncSession,
    *,
    user_message: str,
    provider_errors: list[str],
) -> str:
    """Build a deterministic chat reply from live DB state when LLMs are unavailable."""
    purpose_result = await db.execute(
        select(PurposeRecord).order_by(desc(PurposeRecord.created_at)).limit(1)
    )
    purpose = purpose_result.scalar_one_or_none()

    apps_result = await db.execute(select(AppRecord).order_by(AppRecord.created_at))
    apps = apps_result.scalars().all()

    evts_result = await db.execute(
        select(EvolutionEventRecord)
        .order_by(desc(EvolutionEventRecord.created_at))
        .limit(30)
    )
    evts = evts_result.scalars().all()

    backlog_result = await db.execute(
        select(EvolutionBacklogItemRecord)
        .where(EvolutionBacklogItemRecord.status.in_(["in_progress", "pending", "blocked"]))
        .order_by(EvolutionBacklogItemRecord.sequence, EvolutionBacklogItemRecord.created_at)
        .limit(5)
    )
    backlog_items = backlog_result.scalars().all()

    question = (user_message or "").lower()
    app_lines = [
        f"- {app.icon or '•'} {app.name} [{app.status}] — {len(app.features)} features, {len(app.capabilities)} capabilities. Goal: {_clip(app.goal or app.description or 'No goal recorded.', 140)}"
        for app in apps
    ] or ["- No apps have been recorded yet."]

    failed_events = [evt for evt in evts if evt.status == "failed"]
    failed_lines = [
        f"- {_clip(evt.plan_summary or evt.user_request or 'Unnamed evolution', 140)}"
        + (
            f" Error: {_clip(evt.error, 140)}"
            if evt.error else ""
        )
        for evt in failed_events[:3]
    ] or ["- No failed evolutions in the latest 30 cycles."]

    plan_change_lines: list[str] = []
    for evt in evts:
        changes = (evt.events_json or {}).get("plan_changes", []) if evt.events_json else []
        for change in changes[:5]:
            action = change.get("action", "changed")
            path = change.get("file_path", "unknown file")
            plan_change_lines.append(f"- {action} {path}")
        if plan_change_lines:
            break
    if not plan_change_lines:
        plan_change_lines = ["- No file-level change list is available in recent evolution events."]

    backlog_lines = [
        f"- [{item.status}] {item.title}"
        + (f" — {_clip(item.description, 120)}" if item.description else "")
        for item in backlog_items
    ] or ["- No active backlog items are recorded."]

    purpose_name, purpose_desc = _extract_purpose_identity(
        purpose.content_yaml if purpose else ""
    )
    purpose_lines = [
        f"- Current purpose: {purpose_name}",
        f"- Summary: {_clip(purpose_desc or 'No purpose description recorded.', 220)}",
    ]

    recent_counts = Counter(evt.status for evt in evts)
    status_line = (
        f"- Latest 30 evolution events: "
        f"{recent_counts.get('completed', 0)} completed, "
        f"{recent_counts.get('failed', 0)} failed, "
        f"{sum(1 for evt in evts if evt.status not in {'completed', 'failed'})} non-terminal."
    )
    architecture_lines = [
        "- Frontend: React desktop shell with app windows.",
        "- Backend: FastAPI + PostgreSQL for apps, purpose, evolutions, and settings.",
        "- Engine: MAPE-K loop that plans and applies code changes to /opt/evolved-app.",
    ]

    intro = "The external LLM providers are unavailable right now, so this answer comes from the live system database."
    if provider_errors:
        intro += " " + " ".join(provider_errors)

    sections = [intro]

    if "purpose" in question:
        sections.extend(["", "Purpose", *purpose_lines])
    elif "app" in question and any(
        token in question for token in ["exist", "current", "have", "list", "what"]
    ):
        sections.extend(["", "Apps", *app_lines])
    elif any(token in question for token in ["fail", "failed", "failing", "error"]):
        sections.extend(["", "Failures", status_line, *failed_lines])
    elif any(token in question for token in ["trying to build", "build", "next", "roadmap", "plan"]):
        sections.extend(["", "Backlog", *backlog_lines])
    elif any(token in question for token in ["file", "modified", "created", "changed"]):
        sections.extend(["", "Recent file changes", *plan_change_lines])
    elif "mape-k" in question or "architecture" in question or "how" in question:
        sections.extend(["", "Architecture", *architecture_lines])
    else:
        sections.extend(
            [
                "",
                "Purpose",
                *purpose_lines,
                "",
                "Apps",
                *app_lines[:5],
                "",
                "Backlog",
                *backlog_lines[:5],
                "",
                "Evolution status",
                status_line,
                *failed_lines[:3],
            ]
        )

    return "\n".join(sections)


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


def _to_openai_messages(system: str, messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Translate chat history into a compact transcript for the Responses API."""
    transcript_lines = []
    for message in messages:
        role = message.get("role", "user").upper()
        content = message.get("content", "").strip()
        if content:
            transcript_lines.append(f"{role}: {content}")

    transcript = "\n\n".join(transcript_lines).strip()
    if not transcript:
        transcript = "USER: Hello."

    return [{"role": "user", "content": transcript}]


async def _stream_anthropic(
    api_key: str,
    system: str,
    messages: list[dict],
    *,
    model: str,
) -> AsyncIterator[str]:
    """Call Anthropic API with streaming and yield SSE chunks."""
    payload = {
        "model": model,
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


async def _stream_bedrock(
    system: str,
    messages: list[dict],
    *,
    model: str,
) -> AsyncIterator[str]:
    """Call Bedrock Converse API and return a single SSE text chunk."""
    client = boto3.client("bedrock-runtime", region_name=_BEDROCK_REGION)

    try:
        response = await asyncio.to_thread(
            client.converse,
            modelId=model,
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


async def _stream_openai(
    api_key: str,
    system: str,
    messages: list[dict],
    *,
    model: str,
) -> AsyncIterator[str]:
    """Call OpenAI Responses API and emit a single SSE text chunk."""
    try:
        from openai import AsyncOpenAI
    except Exception as exc:  # pragma: no cover - depends on installed environment
        raise ChatProviderError(f"OpenAI SDK unavailable: {exc}") from exc

    client = AsyncOpenAI(api_key=api_key)

    try:
        response = await client.responses.create(
            model=model,
            instructions=system,
            input=_to_openai_messages(system, messages),
        )
    except Exception as exc:  # pragma: no cover - exact SDK errors depend on runtime
        raise ChatProviderError(str(exc)) from exc

    text = (response.output_text or "").strip()
    if not text:
        raise ChatProviderError("OpenAI returned no text.")

    yield _sse_text(text)
    yield _sse_done()


async def _stream_chat_response(
    *,
    system: str,
    messages: list[dict],
    provider: str,
    model: str,
    anthropic_api_key: str,
    openai_api_key: str,
    local_fallback_text: str | None = None,
) -> AsyncIterator[str]:
    """Try configured providers in order and surface real upstream failures."""
    provider_errors: list[str] = []

    for active_provider in _provider_order(
        provider,
        anthropic_api_key=anthropic_api_key,
        openai_api_key=openai_api_key,
    ):
        try:
            if active_provider == "anthropic":
                anthropic_model = model if provider == "anthropic" else _ANTHROPIC_MODEL
                async for chunk in _stream_anthropic(
                    anthropic_api_key,
                    system,
                    messages,
                    model=anthropic_model,
                ):
                    yield chunk
            elif active_provider == "openai":
                openai_model = model if provider == "openai" else _OPENAI_MODEL
                async for chunk in _stream_openai(
                    openai_api_key,
                    system,
                    messages,
                    model=openai_model,
                ):
                    yield chunk
            else:
                bedrock_model = model if provider == "bedrock" else _BEDROCK_MODEL_ID
                async for chunk in _stream_bedrock(
                    system,
                    messages,
                    model=bedrock_model,
                ):
                    yield chunk
            return
        except ChatProviderError as exc:
            provider_errors.append(f"{active_provider.title()}: {exc}")

    if local_fallback_text:
        yield _sse_text(local_fallback_text)
        yield _sse_done()
        return

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
    runtime = await _get_runtime_settings(db)
    messages = [{"role": m.role, "content": m.content} for m in body.messages]
    local_fallback_text = await _build_local_fallback_reply(
        db,
        user_message=messages[-1]["content"] if messages else "",
        provider_errors=[],
    )

    return StreamingResponse(
        _stream_chat_response(
            system=system,
            messages=messages,
            provider=runtime["llm_provider"],
            model=runtime["llm_model"],
            anthropic_api_key=runtime["anthropic_api_key"],
            openai_api_key=runtime["openai_api_key"],
            local_fallback_text=local_fallback_text,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering for SSE
        },
    )
