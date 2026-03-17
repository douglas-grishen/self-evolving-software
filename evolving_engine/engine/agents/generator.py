"""Code Generator Agent — produces actual source files from an evolution plan.

Responsibilities (MAPE-K: Plan + Execute):
- Receive the EvolutionPlan and RepoMap
- Call the LLM to generate code for each planned file change
- Write generated files to a staging area (not directly to the managed app)
- Support frontend (React/TS), backend (FastAPI/Python), and database (SQL) layers
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field

from engine.agents.base import BaseAgent
from engine.context import EvolutionContext
from engine.models.evolution import EvolutionStatus, GeneratedFile
from engine.providers.base import BaseLLMProvider

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
# Database session — ALWAYS use this, never app.core.* or app.db.*
from app.database import get_db, Base, AsyncSessionLocal
from sqlalchemy.ext.asyncio import AsyncSession

# Auth
from app.auth import get_current_admin

# Config/settings
from app.config import settings

# Existing models (import from their actual files)
from app.models.base import Base          # SQLAlchemy declarative base
from app.models.evolution import EvolutionEvent, Inception, PurposeVersion
from app.models.admin import AdminUser
from app.models.apps import AppRecord, FeatureRecord, CapabilityRecord

# Router registration in app/api/v1/__init__.py
from app.api.v1.<module> import router as <name>_router
v1_router.include_router(<name>_router)
```

⚠️  NEVER use: `from app.core.*`, `from app.db.*`, `from app.models.company import Company`
    — these modules DO NOT EXIST in this codebase.
⚠️  New models must inherit from `Base` imported from `app.models.base`, NOT `app.database`.

## Rules
- Follow existing code conventions visible in the repo map
- Use async/await for all FastAPI endpoints and DB operations
- Use functional React components with TypeScript strict mode
- Include proper imports and type annotations
- Generate working, production-quality code

## FORBIDDEN Paths — NEVER generate files at these paths
⛔ `backend/alembic/versions/` — NEVER create migration files. Schema changes are managed
   by the framework. If the plan requires a new DB table, add the SQLAlchemy model only;
   do NOT generate an alembic migration file.
⛔ `backend/app/core/` — this directory does not exist in this codebase.
⛔ `backend/app/db/` — this directory does not exist in this codebase.
⛔ `backend/app/api/deps.py` — auth deps live in `app/auth.py`, not here.
⛔ `backend/app/api/v1/__init__.py` — do NOT modify the router registry file; the framework
   manages router registration.

Respond with a JSON array of file objects."""


class GeneratedFileList(BaseModel):
    """Wrapper for structured LLM output."""
    files: list[GeneratedFile] = Field(default_factory=list)


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

        # Generate code via LLM
        result = await self.provider.generate_structured(
            system_prompt=SYSTEM_PROMPT,
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
            "backend/alembic/versions/",   # rogue migrations break alembic
            "backend/app/core/",           # directory does not exist
            "backend/app/db/",             # directory does not exist
        )
        FORBIDDEN_EXACT = {
            "backend/app/api/deps.py",
            "backend/app/api/v1/__init__.py",
        }

        allowed_files = []
        for gen_file in result.files:
            fp = gen_file.file_path.lstrip("/")
            if any(fp.startswith(p) for p in FORBIDDEN_PREFIXES) or fp in FORBIDDEN_EXACT:
                self.logger.warning(
                    "generator.blocked_forbidden_path",
                    path=gen_file.file_path,
                )
                continue
            allowed_files.append(gen_file)

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
