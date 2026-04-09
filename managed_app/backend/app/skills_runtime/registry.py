"""Registry and executor for runtime skills."""

from __future__ import annotations

from typing import Any

from app.skills_runtime.base import (
    BaseSkill,
    SkillDisabledError,
    SkillNotFoundError,
)
from app.skills_runtime.models import (
    SkillExecutionContext,
    SkillInvocationRequest,
    SkillInvocationResponse,
    SkillMetadata,
)
from app.skills_runtime.send_email import SendEmailSkill
from app.skills_runtime.web_browser import WebBrowserSkill


class SkillRegistry:
    """In-memory registry for code-local skills."""

    def __init__(self, skills: list[BaseSkill] | None = None) -> None:
        registered = skills or [SendEmailSkill(), WebBrowserSkill()]
        self._skills = {skill.metadata().key: skill for skill in registered}

    def list_skills(self) -> list[BaseSkill]:
        return [self._skills[key] for key in sorted(self._skills)]

    def get(self, key: str) -> BaseSkill:
        skill = self._skills.get(key)
        if skill is None:
            raise SkillNotFoundError(f"Skill '{key}' is not registered")
        return skill


class SkillExecutor:
    """Resolve runtime metadata and execute validated skill invocations."""

    def __init__(self, registry: SkillRegistry | None = None) -> None:
        self.registry = registry or SkillRegistry()

    def metadata_for_record(self, record: Any) -> SkillMetadata:
        runtime = self.registry.get(record.key).metadata()
        return runtime.model_copy(
            update={
                "status": getattr(record, "status", runtime.status),
                "scope": getattr(record, "scope", runtime.scope),
                "executor_kind": getattr(record, "executor_kind", runtime.executor_kind),
                "config_json": getattr(record, "config_json", None) or runtime.config_json,
                "permissions_json": getattr(record, "permissions_json", None)
                or runtime.permissions_json,
            }
        )

    async def invoke(
        self,
        record: Any,
        request: SkillInvocationRequest,
        settings_map: dict[str, str] | None = None,
    ) -> SkillInvocationResponse:
        skill = self.registry.get(record.key)
        metadata = self.metadata_for_record(record)

        if metadata.status != "active":
            raise SkillDisabledError(f"Skill '{metadata.key}' is not active")

        context = SkillExecutionContext(
            skill=metadata,
            settings=settings_map or {},
            request_context=request.context,
        )
        return await skill.invoke(request, context)
