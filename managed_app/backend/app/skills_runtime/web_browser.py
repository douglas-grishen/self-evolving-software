"""Structured browser automation skill backed by Playwright."""

from __future__ import annotations

import base64
import json
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from app.skills_runtime.base import BaseSkill, SkillDisabledError, SkillValidationError
from app.skills_runtime.models import (
    SkillArtifact,
    SkillExecutionContext,
    SkillInvocationRequest,
    SkillInvocationResponse,
    SkillMetadata,
)

try:  # pragma: no cover - exercised in integration/runtime, monkeypatched in unit tests
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover
    PlaywrightTimeoutError = TimeoutError
    async_playwright = None


class BrowserGotoAction(BaseModel):
    type: Literal["goto"]
    url: str


class BrowserClickAction(BaseModel):
    type: Literal["click"]
    selector: str


class BrowserTypeAction(BaseModel):
    type: Literal["type"]
    selector: str
    text: str
    clear: bool = True


class BrowserSelectAction(BaseModel):
    type: Literal["select"]
    selector: str
    value: str


class BrowserWaitForAction(BaseModel):
    type: Literal["wait_for"]
    selector: str | None = None
    text: str | None = None
    milliseconds: int | None = None
    state: Literal["attached", "detached", "hidden", "visible"] = "visible"

    @model_validator(mode="after")
    def _require_one_target(self) -> BrowserWaitForAction:
        if not any((self.selector, self.text, self.milliseconds)):
            raise ValueError("wait_for requires selector, text, or milliseconds")
        return self


class BrowserExtractTextAction(BaseModel):
    type: Literal["extract_text"]
    selector: str
    name: str | None = None


class BrowserScreenshotAction(BaseModel):
    type: Literal["screenshot"]
    name: str = "screenshot"
    selector: str | None = None
    full_page: bool = False


class BrowserEvaluateAction(BaseModel):
    type: Literal["evaluate"]
    expression: str
    name: str | None = None

    @field_validator("expression")
    @classmethod
    def _validate_expression(cls, value: str) -> str:
        expression = value.strip()
        forbidden_tokens = (
            ";",
            "=>",
            "function",
            "fetch(",
            "XMLHttpRequest",
            "document.cookie",
            "localStorage",
            "sessionStorage",
            "eval(",
            "Function(",
            "import(",
        )
        if not expression:
            raise ValueError("expression cannot be blank")
        if "\n" in expression or len(expression) > 200:
            raise ValueError("expression must be a single short expression")
        if any(token in expression for token in forbidden_tokens):
            raise ValueError("expression contains a forbidden token")
        return expression


BrowserAction = Annotated[
    (
        BrowserGotoAction
        | BrowserClickAction
        | BrowserTypeAction
        | BrowserSelectAction
        | BrowserWaitForAction
        | BrowserExtractTextAction
        | BrowserScreenshotAction
        | BrowserEvaluateAction
    ),
    Field(discriminator="type"),
]


class WebBrowserSkillInput(BaseModel):
    actions: list[BrowserAction] = Field(min_length=1, max_length=25)
    headless: bool = True
    timeout_ms: int = Field(default=10000, ge=1000, le=120000)


def _parse_allowed_domains(raw: str | None) -> set[str]:
    if not raw:
        return set()
    value = raw.strip()
    if not value:
        return set()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(parsed, str):
        return {parsed.strip().lower()} if parsed.strip() else set()
    if isinstance(parsed, list):
        return {str(item).strip().lower() for item in parsed if str(item).strip()}
    return set()


def _parse_timeout_ms(payload_timeout_ms: int, raw_timeout_seconds: str | None) -> int:
    try:
        configured_timeout_ms = int((raw_timeout_seconds or "15").strip()) * 1000
    except ValueError:
        configured_timeout_ms = 15000
    return min(payload_timeout_ms, configured_timeout_ms)


class WebBrowserSkill(BaseSkill):
    """Structured browser automation using a constrained action grammar."""

    input_model = WebBrowserSkillInput

    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            key="web-browser",
            name="Web Browser",
            description=(
                "Structured browser automation over Playwright with auditable actions, "
                "screenshots, text extraction, and constrained evaluation."
            ),
            status="active",
            scope="engine_and_apps",
            executor_kind="local",
            config_json={"browser": "chromium"},
            permissions_json={"requires_enabled_setting": "skill_browser_enabled"},
        )

    async def invoke(
        self,
        request: SkillInvocationRequest,
        context: SkillExecutionContext,
    ) -> SkillInvocationResponse:
        payload = self.validate_input(request.input)
        assert isinstance(payload, WebBrowserSkillInput)

        if async_playwright is None:
            raise SkillDisabledError("Playwright is not installed in this runtime")

        enabled = context.settings.get("skill_browser_enabled", "false").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            raise SkillDisabledError("Skill 'web-browser' is disabled by runtime settings")

        if request.dry_run:
            return SkillInvocationResponse(
                ok=True,
                output={"planned_actions": [action.model_dump() for action in payload.actions]},
                logs=["Dry run: browser actions were validated but not executed."],
            )

        timeout_ms = _parse_timeout_ms(
            payload.timeout_ms,
            context.settings.get("skill_browser_timeout_seconds"),
        )
        allowed_domains = _parse_allowed_domains(
            context.settings.get("skill_browser_allowed_domains")
        )
        logs: list[str] = []
        results: list[dict[str, Any]] = []
        artifacts: list[SkillArtifact] = []

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=payload.headless)
            try:
                page = await browser.new_page()
                page.set_default_timeout(timeout_ms)

                for index, action in enumerate(payload.actions, start=1):
                    logs.append(f"{index}. {action.type}")
                    if isinstance(action, BrowserGotoAction):
                        self._validate_navigation(action.url, allowed_domains)
                        await page.goto(action.url, wait_until="domcontentloaded")
                        results.append({"type": action.type, "url": page.url})
                    elif isinstance(action, BrowserClickAction):
                        await page.locator(action.selector).first.click()
                        results.append({"type": action.type, "selector": action.selector})
                    elif isinstance(action, BrowserTypeAction):
                        locator = page.locator(action.selector).first
                        if action.clear:
                            await locator.fill("")
                        await locator.fill(action.text)
                        results.append({"type": action.type, "selector": action.selector})
                    elif isinstance(action, BrowserSelectAction):
                        await page.locator(action.selector).first.select_option(action.value)
                        results.append(
                            {
                                "type": action.type,
                                "selector": action.selector,
                                "value": action.value,
                            }
                        )
                    elif isinstance(action, BrowserWaitForAction):
                        await self._wait_for(page, action)
                        results.append({"type": action.type})
                    elif isinstance(action, BrowserExtractTextAction):
                        text = await page.locator(action.selector).first.inner_text()
                        key = action.name or action.selector
                        results.append({"type": action.type, "name": key, "text": text})
                    elif isinstance(action, BrowserScreenshotAction):
                        image = await self._take_screenshot(page, action)
                        artifact_name = f"{action.name}.png"
                        artifacts.append(
                            SkillArtifact(
                                name=artifact_name,
                                kind="image",
                                content_type="image/png",
                                data=base64.b64encode(image).decode("ascii"),
                                encoding="base64",
                            )
                        )
                        results.append({"type": action.type, "artifact": artifact_name})
                    elif isinstance(action, BrowserEvaluateAction):
                        value = await page.evaluate(f"() => ({action.expression})")
                        results.append(
                            {
                                "type": action.type,
                                "name": action.name or "result",
                                "value": value,
                            }
                        )

                return SkillInvocationResponse(
                    ok=True,
                    output={"results": results, "final_url": page.url},
                    artifacts=artifacts,
                    logs=logs,
                )
            except PlaywrightTimeoutError as exc:
                return SkillInvocationResponse(
                    ok=False,
                    output={"results": results},
                    artifacts=artifacts,
                    logs=logs,
                    error=f"Browser action timed out: {exc}",
                )
            except Exception as exc:
                return SkillInvocationResponse(
                    ok=False,
                    output={"results": results},
                    artifacts=artifacts,
                    logs=logs,
                    error=str(exc),
                )
            finally:
                await browser.close()

    def _validate_navigation(self, url: str, allowed_domains: set[str]) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SkillValidationError("goto requires an absolute http(s) URL")
        if not allowed_domains:
            return
        host = (parsed.hostname or "").lower()
        if host not in allowed_domains:
            raise SkillValidationError(
                f"Navigation to domain '{host}' is not allowed by runtime policy"
            )

    async def _wait_for(self, page: Any, action: BrowserWaitForAction) -> None:
        if action.selector:
            await page.wait_for_selector(action.selector, state=action.state)
            return
        if action.text:
            await page.get_by_text(action.text).first.wait_for()
            return
        await page.wait_for_timeout(action.milliseconds or 0)

    async def _take_screenshot(self, page: Any, action: BrowserScreenshotAction) -> bytes:
        if action.selector:
            return await page.locator(action.selector).first.screenshot()
        return await page.screenshot(full_page=action.full_page)
