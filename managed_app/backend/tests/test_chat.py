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
            anthropic_api_key="test-key",
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
            anthropic_api_key="test-key",
        )
    )

    assert len(chunks) == 2
    assert "Anthropic: Your credit balance is too low." in chunks[0]
    assert "Bedrock: Unable to locate credentials." in chunks[0]
    assert chunks[1] == "data: [DONE]\n\n"
