"""Tests for runtime skills execution and validation."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from app.skills_runtime import (
    SkillDisabledError,
    SkillExecutor,
    SkillInvocationRequest,
    SkillRegistry,
)
from app.skills_runtime.send_email import SendEmailSkill
from app.skills_runtime.web_browser import WebBrowserSkill


class _FakeLocator:
    def __init__(self, page, selector: str):
        self.page = page
        self.selector = selector
        self.first = self

    async def click(self):
        self.page.events.append(("click", self.selector))

    async def fill(self, text: str):
        self.page.events.append(("fill", self.selector, text))

    async def select_option(self, value: str):
        self.page.events.append(("select", self.selector, value))

    async def inner_text(self):
        return f"text:{self.selector}"

    async def screenshot(self):
        return b"locator-shot"

    async def wait_for(self):
        self.page.events.append(("wait_for_text", self.selector))


class _FakePage:
    def __init__(self):
        self.url = "about:blank"
        self.events: list[tuple] = []
        self.timeout_ms = None

    def set_default_timeout(self, timeout_ms: int):
        self.timeout_ms = timeout_ms

    async def goto(self, url: str, wait_until: str | None = None):
        self.url = url
        self.events.append(("goto", url, wait_until))

    def locator(self, selector: str):
        return _FakeLocator(self, selector)

    def get_by_text(self, text: str):
        return _FakeLocator(self, text)

    async def wait_for_selector(self, selector: str, state: str = "visible"):
        self.events.append(("wait_for_selector", selector, state))

    async def wait_for_timeout(self, milliseconds: int):
        self.events.append(("wait_for_timeout", milliseconds))

    async def screenshot(self, full_page: bool = False):
        self.events.append(("page_screenshot", full_page))
        return b"page-shot"

    async def evaluate(self, _expression: str):
        return "Example Title"


class _FakeBrowser:
    def __init__(self):
        self.page = _FakePage()

    async def new_page(self):
        return self.page

    async def close(self):
        return None


class _FakePlaywrightContext:
    async def __aenter__(self):
        chromium = SimpleNamespace(launch=self._launch)
        return SimpleNamespace(chromium=chromium)

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def _launch(self, headless: bool = True):
        browser = _FakeBrowser()
        browser.page.events.append(("launch", headless))
        return browser


@pytest.mark.asyncio
async def test_web_browser_skill_executes_structured_actions(monkeypatch):
    """The browser skill should execute typed actions and return artifacts/results."""
    monkeypatch.setattr(
        "app.skills_runtime.web_browser.async_playwright",
        lambda: _FakePlaywrightContext(),
    )

    skill = WebBrowserSkill()
    response = await skill.invoke(
        SkillInvocationRequest(
            input={
                "actions": [
                    {"type": "goto", "url": "https://example.com"},
                    {"type": "extract_text", "selector": "h1", "name": "heading"},
                    {"type": "screenshot", "name": "home"},
                    {"type": "evaluate", "expression": "document.title", "name": "title"},
                ],
                "timeout_ms": 5000,
            }
        ),
        SimpleNamespace(
            settings={
                "skill_browser_enabled": "true",
                "skill_browser_timeout_seconds": "15",
                "skill_browser_allowed_domains": '["example.com"]',
            },
            skill=skill.metadata(),
            request_context={},
        ),
    )

    assert response.ok is True
    assert response.output["final_url"] == "https://example.com"
    assert response.output["results"][0]["type"] == "goto"
    assert response.output["results"][1]["text"] == "text:h1"
    assert response.output["results"][2]["artifact"] == "home.png"
    assert response.output["results"][3]["value"] == "Example Title"
    assert response.artifacts[0].name == "home.png"
    assert base64.b64decode(response.artifacts[0].data) == b"page-shot"


@pytest.mark.asyncio
async def test_web_browser_skill_rejects_disabled_setting():
    """The browser skill should hard-fail when the runtime toggle is off."""
    skill = WebBrowserSkill()

    with pytest.raises(SkillDisabledError):
        await skill.invoke(
            SkillInvocationRequest(input={"actions": [{"type": "goto", "url": "https://example.com"}]}),
            SimpleNamespace(
                settings={"skill_browser_enabled": "false"},
                skill=skill.metadata(),
                request_context={},
            ),
        )


@pytest.mark.asyncio
async def test_web_browser_skill_blocks_disallowed_domain(monkeypatch):
    """Domain allowlists should prevent navigation to unapproved hosts."""
    monkeypatch.setattr(
        "app.skills_runtime.web_browser.async_playwright",
        lambda: _FakePlaywrightContext(),
    )
    skill = WebBrowserSkill()

    response = await skill.invoke(
        SkillInvocationRequest(input={"actions": [{"type": "goto", "url": "https://blocked.example"}]}),
        SimpleNamespace(
            settings={
                "skill_browser_enabled": "true",
                "skill_browser_allowed_domains": "example.com",
                "skill_browser_timeout_seconds": "15",
            },
            skill=skill.metadata(),
            request_context={},
        ),
    )

    assert response.ok is False
    assert "not allowed" in (response.error or "")


@pytest.mark.asyncio
async def test_skill_executor_rejects_inactive_record():
    """Persistent skill status should gate execution independently of runtime code."""
    executor = SkillExecutor(SkillRegistry())
    record = SimpleNamespace(
        key="web-browser",
        status="disabled",
        scope="engine_and_apps",
        executor_kind="local",
        config_json={},
        permissions_json={},
    )

    with pytest.raises(SkillDisabledError):
        await executor.invoke(
            record,
            SkillInvocationRequest(input={"actions": [{"type": "goto", "url": "https://example.com"}]}),
            settings_map={"skill_browser_enabled": "true"},
        )


@pytest.mark.asyncio
async def test_send_email_skill_sends_via_resend(monkeypatch):
    """The send-email skill should use Resend and report the returned message id."""

    sent_payloads: list[dict[str, object]] = []

    class _Emails:
        @staticmethod
        def send(payload):
            sent_payloads.append(payload)
            return {"id": "email_123"}

    class _FakeResend:
        api_key = None
        Emails = _Emails

    monkeypatch.setattr("app.skills_runtime.send_email.resend", _FakeResend)

    skill = SendEmailSkill()
    response = await skill.invoke(
        SkillInvocationRequest(
            input={
                "to": ["ada@example.com"],
                "subject": "Launch update",
                "text": "System is live.",
            }
        ),
        SimpleNamespace(
            settings={
                "skill_email_enabled": "true",
                "skill_email_resend_api_key": "re_test_key",
                "skill_email_default_from": "noreply@example.com",
            },
            skill=skill.metadata(),
            request_context={},
        ),
    )

    assert response.ok is True
    assert response.output["id"] == "email_123"
    assert sent_payloads == [
        {
            "from": "noreply@example.com",
            "to": ["ada@example.com"],
            "subject": "Launch update",
            "text": "System is live.",
        }
    ]


@pytest.mark.asyncio
async def test_send_email_skill_requires_api_key(monkeypatch):
    """Missing Resend API keys should fail as a disabled runtime configuration."""
    monkeypatch.setattr("app.skills_runtime.send_email.resend", object())

    skill = SendEmailSkill()

    with pytest.raises(SkillDisabledError):
        await skill.invoke(
            SkillInvocationRequest(
                input={
                    "to": ["ada@example.com"],
                    "subject": "Launch update",
                    "text": "System is live.",
                }
            ),
            SimpleNamespace(
                settings={
                    "skill_email_enabled": "true",
                    "skill_email_resend_api_key": "",
                },
                skill=skill.metadata(),
                request_context={},
            ),
        )
