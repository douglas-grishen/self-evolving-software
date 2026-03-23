"""Tests for chat provider fallback behavior."""

import pytest

from app.api.v1 import chat as chat_api


async def _collect_chunks(generator):
    chunks = []
    async for chunk in generator:
        chunks.append(chunk)
    return chunks


@pytest.mark.asyncio
async def test_stream_chat_response_falls_back_to_bedrock(monkeypatch):
    """Anthropic failures should fall back to Bedrock instead of returning blank output."""

    async def fake_anthropic(*args, **kwargs):
        raise chat_api.ChatProviderError("Your credit balance is too low.")
        yield  # pragma: no cover - keeps async-generator shape

    async def fake_bedrock(*args, **kwargs):
        yield chat_api._sse_text("Competitive Intelligence exists.")
        yield chat_api._sse_done()

    monkeypatch.setattr(chat_api, "_stream_anthropic", fake_anthropic)
    monkeypatch.setattr(chat_api, "_stream_bedrock", fake_bedrock)

    chunks = await _collect_chunks(
        chat_api._stream_chat_response(
            system="system",
            messages=[{"role": "user", "content": "What apps currently exist?"}],
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            anthropic_api_key="test-key",
            openai_api_key="",
        )
    )

    assert chunks == [
        'data: {"text": "Competitive Intelligence exists."}\n\n',
        "data: [DONE]\n\n",
    ]


@pytest.mark.asyncio
async def test_stream_chat_response_surfaces_provider_errors(monkeypatch):
    """If all providers fail, the user should receive an explicit error message."""

    async def fake_anthropic(*args, **kwargs):
        raise chat_api.ChatProviderError("Your credit balance is too low.")
        yield  # pragma: no cover - keeps async-generator shape

    async def fake_bedrock(*args, **kwargs):
        raise chat_api.ChatProviderError("Unable to locate credentials.")
        yield  # pragma: no cover - keeps async-generator shape

    monkeypatch.setattr(chat_api, "_stream_anthropic", fake_anthropic)
    monkeypatch.setattr(chat_api, "_stream_bedrock", fake_bedrock)

    chunks = await _collect_chunks(
        chat_api._stream_chat_response(
            system="system",
            messages=[{"role": "user", "content": "What apps currently exist?"}],
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            anthropic_api_key="test-key",
            openai_api_key="",
        )
    )

    assert len(chunks) == 2
    assert "Anthropic: Your credit balance is too low." in chunks[0]
    assert "Bedrock: Unable to locate credentials." in chunks[0]
    assert chunks[1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_stream_chat_response_prefers_bedrock_when_configured(monkeypatch):
    """Production can bypass exhausted Anthropic credits and hit Bedrock first."""

    calls: list[str] = []

    async def fake_anthropic(*args, **kwargs):
        calls.append("anthropic")
        yield chat_api._sse_text("Anthropic reply")
        yield chat_api._sse_done()

    async def fake_bedrock(*args, **kwargs):
        calls.append("bedrock")
        yield chat_api._sse_text("Bedrock reply")
        yield chat_api._sse_done()

    monkeypatch.setenv("ENGINE_LLM_PROVIDER", "bedrock")
    monkeypatch.setattr(chat_api, "_stream_anthropic", fake_anthropic)
    monkeypatch.setattr(chat_api, "_stream_bedrock", fake_bedrock)

    chunks = await _collect_chunks(
        chat_api._stream_chat_response(
            system="system",
            messages=[{"role": "user", "content": "What apps currently exist?"}],
            provider="bedrock",
            model="bedrock-model",
            anthropic_api_key="test-key",
            openai_api_key="",
        )
    )

    assert calls == ["bedrock"]
    assert chunks == [
        'data: {"text": "Bedrock reply"}\n\n',
        "data: [DONE]\n\n",
    ]


@pytest.mark.asyncio
async def test_stream_chat_response_uses_local_fallback_when_providers_fail(monkeypatch):
    """A deterministic local summary should keep chat usable without external LLMs."""

    async def fake_anthropic(*args, **kwargs):
        raise chat_api.ChatProviderError("Anthropic unavailable.")
        yield  # pragma: no cover

    async def fake_bedrock(*args, **kwargs):
        raise chat_api.ChatProviderError("Bedrock unavailable.")
        yield  # pragma: no cover

    monkeypatch.setattr(chat_api, "_stream_anthropic", fake_anthropic)
    monkeypatch.setattr(chat_api, "_stream_bedrock", fake_bedrock)

    chunks = await _collect_chunks(
        chat_api._stream_chat_response(
            system="system",
            messages=[{"role": "user", "content": "What apps currently exist?"}],
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            anthropic_api_key="test-key",
            openai_api_key="",
            local_fallback_text="Live apps:\n- Competitive Intelligence",
        )
    )

    assert chunks == [
        'data: {"text": "Live apps:\\n- Competitive Intelligence"}\n\n',
        "data: [DONE]\n\n",
    ]


@pytest.mark.asyncio
async def test_stream_chat_response_prefers_openai_when_selected(monkeypatch):
    """OpenAI should be used first when selected in runtime settings."""

    calls: list[str] = []

    async def fake_openai(*args, **kwargs):
        calls.append("openai")
        yield chat_api._sse_text("OpenAI reply")
        yield chat_api._sse_done()

    async def fake_anthropic(*args, **kwargs):
        calls.append("anthropic")
        yield chat_api._sse_text("Anthropic reply")
        yield chat_api._sse_done()

    monkeypatch.setattr(chat_api, "_stream_openai", fake_openai)
    monkeypatch.setattr(chat_api, "_stream_anthropic", fake_anthropic)

    chunks = await _collect_chunks(
        chat_api._stream_chat_response(
            system="system",
            messages=[{"role": "user", "content": "What apps currently exist?"}],
            provider="openai",
            model="gpt-5.2",
            anthropic_api_key="anthropic-key",
            openai_api_key="openai-key",
        )
    )

    assert calls == ["openai"]
    assert chunks == [
        'data: {"text": "OpenAI reply"}\n\n',
        "data: [DONE]\n\n",
    ]
