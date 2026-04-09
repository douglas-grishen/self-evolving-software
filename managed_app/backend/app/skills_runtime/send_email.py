"""Transactional email skill backed by Resend."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.skills_runtime.base import BaseSkill, SkillDisabledError, SkillValidationError
from app.skills_runtime.models import (
    SkillExecutionContext,
    SkillInvocationRequest,
    SkillInvocationResponse,
    SkillMetadata,
)

try:  # pragma: no cover - exercised through runtime/integration, monkeypatched in tests
    import resend
except Exception:  # pragma: no cover
    resend = None


class SendEmailSkillInput(BaseModel):
    to: list[str] = Field(min_length=1, max_length=50)
    subject: str = Field(min_length=1, max_length=200)
    html: str | None = None
    text: str | None = None
    from_email: str | None = None
    reply_to: str | None = None
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)

    @field_validator("to", "cc", "bcc", mode="before")
    @classmethod
    def _normalize_recipients(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value]
        return value

    @model_validator(mode="after")
    def _require_body(self) -> SendEmailSkillInput:
        if not (self.html or self.text):
            raise ValueError("send-email requires html or text content")
        return self


def _is_enabled(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


class SendEmailSkill(BaseSkill):
    """Send email through Resend using an API key stored in runtime settings."""

    input_model = SendEmailSkillInput

    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            key="send-email",
            name="Send Email",
            description="Transactional email delivery through Resend.",
            status="active",
            scope="engine_and_apps",
            executor_kind="local",
            config_json={"provider": "resend"},
            permissions_json={
                "requires_enabled_setting": "skill_email_enabled",
                "requires_secret_setting": "skill_email_resend_api_key",
            },
        )

    async def invoke(
        self,
        request: SkillInvocationRequest,
        context: SkillExecutionContext,
    ) -> SkillInvocationResponse:
        payload = self.validate_input(request.input)
        assert isinstance(payload, SendEmailSkillInput)

        if resend is None:
            raise SkillDisabledError("Resend is not installed in this runtime")

        if not _is_enabled(context.settings.get("skill_email_enabled")):
            raise SkillDisabledError("Skill 'send-email' is disabled by runtime settings")

        api_key = (context.settings.get("skill_email_resend_api_key") or "").strip()
        if not api_key:
            raise SkillDisabledError("Skill 'send-email' requires skill_email_resend_api_key")

        from_email = (
            (payload.from_email or "").strip()
            or (context.settings.get("skill_email_default_from") or "").strip()
        )
        if not from_email:
            raise SkillValidationError(
                "send-email requires from_email in the request or "
                "skill_email_default_from in settings"
            )

        if request.dry_run:
            return SkillInvocationResponse(
                ok=True,
                output={
                    "provider": "resend",
                    "to": payload.to,
                    "subject": payload.subject,
                    "from_email": from_email,
                },
                logs=["Dry run: email payload validated but not sent."],
            )

        resend.api_key = api_key
        params: dict[str, Any] = {
            "from": from_email,
            "to": payload.to,
            "subject": payload.subject,
        }
        if payload.html:
            params["html"] = payload.html
        if payload.text:
            params["text"] = payload.text
        if payload.reply_to:
            params["reply_to"] = payload.reply_to
        if payload.cc:
            params["cc"] = payload.cc
        if payload.bcc:
            params["bcc"] = payload.bcc

        response = resend.Emails.send(params)
        response_data = self._normalize_response(response)

        return SkillInvocationResponse(
            ok=True,
            output={
                "provider": "resend",
                "id": response_data.get("id"),
                "to": payload.to,
                "subject": payload.subject,
                "from_email": from_email,
                "response": response_data,
            },
            logs=[f"Sent email to {len(payload.to)} recipient(s) via Resend."],
        )

    def _normalize_response(self, response: Any) -> dict[str, Any]:
        if isinstance(response, dict):
            return response
        if hasattr(response, "model_dump"):
            return response.model_dump()
        if hasattr(response, "__dict__"):
            return {
                key: value
                for key, value in vars(response).items()
                if not key.startswith("_")
            }
        return {"raw": str(response)}
