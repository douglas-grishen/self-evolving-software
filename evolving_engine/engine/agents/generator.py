"""Code Generator Agent — produces actual source files from an evolution plan.

Responsibilities (MAPE-K: Plan + Execute):
- Receive the EvolutionPlan and RepoMap
- Call the LLM to generate code for each planned file change
- Write generated files to a staging area (not directly to the managed app)
- Support frontend (React/TS), backend (FastAPI/Python), and database (SQL) layers
"""

import json
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from engine.agents.base import BaseAgent
from engine.context import EvolutionContext
from engine.models.repo_map import FileNode, RepoMap
from engine.models.evolution import EvolutionStatus, GeneratedFile
from engine.models.memory import EngineMemory
from engine.providers.base import BaseLLMProvider
from engine.repo.scanner import canonicalize_frontend_app_key

SYSTEM_PROMPT = """You are an expert full-stack code generator for a self-evolving software system.

The managed application stack:
- Frontend: React 19 + TypeScript (Vite, functional components, hooks)
- Backend: FastAPI (Python 3.11+, SQLAlchemy 2.0 async, Pydantic v2)
- Database: PostgreSQL with Alembic migrations

You receive:
1. An evolution plan specifying which files to create/modify
2. A repository map showing the current codebase state

For each file in the plan, you must produce the COMPLETE file content.
- For new files: generate the full file
- For modifications: generate the entire updated file (not a diff)

## CRITICAL: Backend Import Conventions (use EXACTLY these paths)

```python
# Database session — ALWAYS use this pattern for FastAPI endpoints
from app.database import get_db          # FastAPI Depends dependency
from sqlalchemy.ext.asyncio import AsyncSession

# Auth
from app.auth import get_current_admin

# Config/settings
from app.config import settings

# Existing models (import from their actual files)
from app.models.base import Base                     # SQLAlchemy declarative base
from app.models.evolution import EvolutionEventRecord, InceptionRecord, PurposeRecord
from app.models.admin import AdminUser
from app.models.apps import AppRecord, FeatureRecord, CapabilityRecord

# New model modules can be imported directly after you create them
# Example: from app.models.company import Company

# New router files are auto-discovered — just define router = APIRouter(...)
# Do NOT touch app/api/v1/__init__.py
```

⚠️  NEVER use: `from app.core.*` or `from app.db.*`
    — these modules DO NOT EXIST in this codebase.
⚠️  NEVER import `AsyncSessionLocal` from app.database — it does not exist. Use `get_db`.
⚠️  New models must inherit from `Base` imported from `app.models.base`, NOT `app.database`.
⚠️  NEVER use `metadata` as a SQLAlchemy column name — it is reserved by DeclarativeBase.

## Rules
- Follow existing code conventions visible in the repo map
- Use async/await for all FastAPI endpoints and DB operations
- Use functional React components with TypeScript strict mode
- Include proper imports and type annotations
- Generate working, production-quality code

## FORBIDDEN Paths — NEVER generate files at these paths
⛔ `backend/app/core/` — this directory does not exist in this codebase.
⛔ `backend/app/db/` — this directory does not exist in this codebase.
⛔ `backend/app/api/deps.py` — auth deps live in `app/auth.py`, not here.
⛔ `backend/app/api/v1/__init__.py` — do NOT modify the router registry file; the framework
   manages router registration.
⛔ `backend/app/models/__init__.py` — do NOT modify the models package init; the framework
   manages which models are loaded.
⛔ `backend/app/config.py` — core application settings; modifying this breaks Alembic and the
   entire backend startup. Never overwrite it.
⛔ `backend/alembic/env.py` — Alembic environment config; never overwrite it.

## Database migrations
- If `requires_migration` is true in the plan, you MUST generate exactly one Alembic
  revision file under `backend/alembic/versions/`.
- Use a concrete filename from the plan, and include both `upgrade()` and `downgrade()`.
- Use the Alembic revision chain from the repo map. The new migration must set a unique
  `revision` and `down_revision` equal to the current single head revision.
- Never use `down_revision = None` unless the repo has no existing Alembic revisions.
- Never create a second Alembic head.
- Keep migrations backwards-compatible and deterministic. Prefer `op.create_table`,
  `op.add_column`, `op.create_index`, etc.
- Do not rely on editing `backend/app/models/__init__.py` or `backend/alembic/env.py`.
- A schema-changing plan without its migration will be rejected by validation.
- `frontend/src/App.tsx` and `frontend/src/App.css` define the operating-system-like desktop
  shell. Do NOT replace or repurpose them for a product app unless the request explicitly asks
  to redesign the desktop shell itself.
- The desktop system windows are framework-owned resilience capabilities. Do NOT remove or break
  Chat, Cost, Settings, Health, Timeline, Purpose, Tasks, Database, or Inceptions while
  implementing product work.
- Product app UIs must be added under `frontend/src/apps/<app-slug>/` and export a default
  component from `frontend/src/apps/<app-slug>/index.ts` or `index.tsx`.
- Those app modules are opened through the existing `frontend/src/components/AppViewer.tsx`
  integration point instead of replacing the desktop shell.
- The desktop app registry lives in `frontend/src/apps/registry.tsx`. Do not recreate or write
  to `frontend/src/config/apps.ts` unless the repository map explicitly shows that file exists.
- The repository map is the source of truth for existing frontend app module roots. Reuse the
  exact path it reports for an app and never create a sibling module that differs only by case,
  camelCase, spacing, or hyphenation.
- The repository map is also the source of truth for notable static assets in `frontend/public/`.
  Reuse those exact asset paths; do not invent duplicate copies under app-module directories
  unless the plan explicitly migrates every consumer to the new location.
- Match the desktop app's frontend module key to a slug of the app name (for example
  `Competitive Intelligence` -> `competitive-intelligence`) so the shell can launch it reliably.
- If the repository map reports a path conflict for a frontend app root, the safe move is to
  consolidate or stabilize that app instead of adding more files under a new variant path.
- If a mounted app depends on backend endpoints, preserve that route contract. When the data
  layer is incomplete, return a valid empty-state response rather than leaving the frontend to
  hit HTTP 404.

Respond with a JSON array of file objects."""


# The sentinel marks where lessons will be inserted in the system prompt.
# Using partition() ensures the insertion point is robust to whitespace changes.
_SENTINEL = "\nRespond with a JSON array of file objects."
_MAX_INJECTED_LESSONS = 30
_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}
_FRONTEND_APPS_PREFIX = "frontend/src/apps/"
_LEGACY_APP_REGISTRY_PATH = "frontend/src/config/apps.ts"
_CANONICAL_APP_REGISTRY_PATH = "frontend/src/apps/registry.tsx"


def _build_lessons_section(lessons: list[EngineMemory]) -> str:
    """Build the lessons block to inject into the generator system prompt.

    Only critical and warning lessons are injected (info is UI-only, never LLM context).
    Sorted: critical first, then by times_reinforced descending (most-repeated first).
    Hard-capped at _MAX_INJECTED_LESSONS to prevent context bloat (~4500 tokens max).
    Returns empty string when there are no injectable lessons.
    """
    injectable = [l for l in lessons if l.severity in ("critical", "warning")]
    injectable.sort(
        key=lambda l: (_SEVERITY_RANK.get(l.severity, 9), -l.times_reinforced)
    )
    injectable = injectable[:_MAX_INJECTED_LESSONS]

    if not injectable:
        return ""

    lines = [
        f"- [{l.severity.upper()}] {l.title}: {l.content}"
        for l in injectable
    ]
    return (
        "\n\n## Lessons Learned — DO NOT Repeat These Mistakes\n"
        "The following patterns have caused failures in previous evolution cycles. "
        "Violating any CRITICAL item will cause immediate validation failure.\n\n"
        + "\n".join(lines)
        + "\n"
    )


class GeneratedFileList(BaseModel):
    """Wrapper for structured LLM output."""
    files: list[GeneratedFile] = Field(default_factory=list)


def _iter_repo_paths(node: FileNode | None, prefix: str = "") -> set[str]:
    if node is None:
        return set()

    name = node.name.strip("./")
    current = "/".join(part for part in [prefix, name] if part).strip("/")
    if node.is_dir:
        result: set[str] = set()
        for child in node.children:
            result.update(_iter_repo_paths(child, current))
        return result

    return {current}


def _repo_has_path(repo_map: RepoMap | None, relative_path: str) -> bool:
    if repo_map is None:
        return False
    return relative_path in _iter_repo_paths(repo_map.tree)


def _normalize_frontend_file_path(path: str, repo_map: RepoMap | None) -> str:
    normalized = path.lstrip("/").replace("\\", "/")

    if normalized == _LEGACY_APP_REGISTRY_PATH and (
        _repo_has_path(repo_map, _CANONICAL_APP_REGISTRY_PATH)
        and not _repo_has_path(repo_map, _LEGACY_APP_REGISTRY_PATH)
    ):
        return _CANONICAL_APP_REGISTRY_PATH

    if not normalized.startswith(_FRONTEND_APPS_PREFIX):
        return normalized

    suffix = normalized[len(_FRONTEND_APPS_PREFIX):]
    module_root, separator, remainder = suffix.partition("/")
    if not module_root:
        return normalized

    canonical_root = canonicalize_frontend_app_key(module_root)
    if canonical_root == module_root:
        return normalized

    return (
        f"{_FRONTEND_APPS_PREFIX}{canonical_root}/{remainder}"
        if separator
        else f"{_FRONTEND_APPS_PREFIX}{canonical_root}"
    )


class CodeGeneratorAgent(BaseAgent):
    """Generates source code files based on the evolution plan."""

    def __init__(self, provider: BaseLLMProvider, **kwargs) -> None:
        super().__init__(**kwargs)
        self.provider = provider

    @property
    def name(self) -> str:
        return "generator"

    async def _execute(self, ctx: EvolutionContext) -> EvolutionContext:
        """Generate code for each file change in the evolution plan."""
        if not ctx.plan:
            return ctx.fail("No evolution plan found — Leader Agent must run first")

        if not ctx.repo_map:
            return ctx.fail("No repo map found — Data Manager Agent must run first")

        # Build context for the LLM
        plan_json = ctx.plan.model_dump_json(indent=2)
        repo_context = ctx.repo_map.to_context_string()

        # Include validation feedback if this is a retry
        feedback = ""
        if ctx.retry_count > 0 and ctx.validation_result:
            feedback = (
                f"\n\n## Previous Attempt Failed (retry {ctx.retry_count})\n"
                f"Errors:\n" + "\n".join(f"- {e}" for e in ctx.validation_result.errors)
            )
            if ctx.validation_result.suggestions:
                feedback += (
                    "\nSuggestions:\n"
                    + "\n".join(f"- {s}" for s in ctx.validation_result.suggestions)
                )

        user_prompt = (
            f"## Evolution Plan\n```json\n{plan_json}\n```\n\n"
            f"## Repository Map\n{repo_context}"
            f"{feedback}\n\n"
            f"Generate the complete file contents for each change in the plan."
        )

        # Compose system prompt: static base + dynamic lessons section
        lessons_section = _build_lessons_section(ctx.lessons)
        if lessons_section:
            base, _, end = SYSTEM_PROMPT.partition(_SENTINEL)
            effective_system = base + lessons_section + _SENTINEL
            self.logger.debug(
                "generator.lessons_injected",
                count=sum(
                    1 for l in ctx.lessons if l.severity in ("critical", "warning")
                ),
            )
        else:
            effective_system = SYSTEM_PROMPT

        # Generate code via LLM
        result = await self.provider.generate_structured(
            system_prompt=effective_system,
            user_prompt=user_prompt,
            response_model=GeneratedFileList,
            max_tokens=self.config.max_tokens,
        )

        self.logger.info(
            "code.generated",
            num_files=len(result.files),
            layers=list({f.layer for f in result.files}),
        )
        # Note: path filtering happens below; allowed_files may be fewer

        # Paths the engine must never write — these would break the managed app
        FORBIDDEN_PREFIXES = (
            "backend/app/core/",           # directory does not exist
            "backend/app/db/",             # directory does not exist
        )
        FORBIDDEN_EXACT = {
            "backend/app/api/deps.py",
            "backend/app/api/v1/__init__.py",  # framework manages router registration
            "backend/app/models/__init__.py",  # framework manages model registration
            "backend/app/config.py",           # core settings — engine overwrites break alembic
            "backend/alembic/env.py",          # alembic env — must not be overwritten
        }

        normalized_files: OrderedDict[str, GeneratedFile] = OrderedDict()
        for gen_file in result.files:
            fp = gen_file.file_path.lstrip("/")
            if any(fp.startswith(p) for p in FORBIDDEN_PREFIXES) or fp in FORBIDDEN_EXACT:
                self.logger.warning(
                    "generator.blocked_forbidden_path",
                    path=gen_file.file_path,
                )
                continue

            normalized_path = _normalize_frontend_file_path(fp, ctx.repo_map)
            if normalized_path != fp:
                self.logger.info(
                    "generator.normalized_path",
                    from_path=gen_file.file_path,
                    to_path=normalized_path,
                )
            normalized_file = gen_file.model_copy(update={"file_path": normalized_path})
            normalized_files[normalized_path] = normalized_file

        allowed_files = list(normalized_files.values())
        skipped = len(result.files) - len(allowed_files)
        if skipped:
            self.logger.warning("generator.forbidden_files_skipped", count=skipped)

        # Write generated files to the staging workspace
        workspace = Path(self.config.workspace_path) / ctx.request_id
        workspace.mkdir(parents=True, exist_ok=True)

        for gen_file in allowed_files:
            target = workspace / gen_file.file_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(gen_file.content)
            self.logger.debug("file.written", path=gen_file.file_path, action=gen_file.action)

        return ctx.model_copy(
            update={
                "generated_files": allowed_files,
                "status": EvolutionStatus.VALIDATING,
            }
        )
