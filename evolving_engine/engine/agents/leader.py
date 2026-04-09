"""Leader Agent — receives user requests and produces an evolution plan.

Responsibilities (MAPE-K: Monitor + Plan):
- Interpret the user's natural language request
- Evaluate the request against the system's Purpose
- Decide whether the request requires code evolution
- Produce a structured EvolutionPlan with file-level changes
- Orchestrate the overall strategy
"""

from __future__ import annotations

from collections import OrderedDict

from engine.agents.base import BaseAgent
from engine.context import EvolutionContext
from engine.models.evolution import EvolutionPlan, EvolutionStatus, FileChange
from engine.models.framework_invariants import FrameworkInvariants
from engine.models.purpose import Purpose
from engine.models.repo_map import FileNode, RepoMap
from engine.providers.base import BaseLLMProvider
from engine.repo.scanner import canonicalize_frontend_app_key

SYSTEM_PROMPT = """You are the Lead Architect of a self-evolving software system.

Your job is to analyze a user's feature request and produce a structured evolution plan.
Every decision you make must align with:
1. The framework invariants — non-negotiable platform and safety rules shared by every instance
2. The system's Purpose — the instance-specific specification that defines
   what the system must achieve and maintain

The managed application stack:
- Frontend: React + TypeScript (Vite)
- Backend: FastAPI (Python) with SQLAlchemy ORM
- Database: PostgreSQL with Alembic migrations

You receive:
1. The framework invariants
2. The system's Purpose
3. The user's request in natural language
4. A repository map showing the current state of the codebase

Before producing a plan, evaluate the request against both the invariants and the Purpose:
- Does it violate any framework/platform invariants?
- Does it violate any safety invariants or operator invariants?
- Does it align with the functional and technical requirements?
- Does it violate any security requirements or constraints?
- Does it follow the evolution directives?
If the request conflicts with the invariants or the Purpose, note this in your reasoning and
adjust the plan to stay within those boundaries.

You must produce a JSON evolution plan specifying:
- summary: a one-line description of the change
- changes: a list of file-level changes (path, action, description, layer)
- requires_migration: whether a new Alembic migration is needed
- requires_new_dependencies: whether new packages must be installed
- risk_level: "low", "medium", or "high"
- reasoning: your thought process (including how this aligns with the Purpose)

IMPORTANT CONSTRAINTS:
- Maximum 5 files per evolution plan. Focus on the most critical changes.
- If a feature requires more than 5 files, break it down: plan only the foundational
  files first. The next evolution cycle will handle the rest.
- Prefer small, incremental changes that can be validated independently.
- Each file change should be self-contained and not break existing functionality.
- New API routers placed in `backend/app/api/v1/` are auto-registered by the framework.
  Do not plan changes to `backend/app/api/v1/__init__.py`.
- Do not plan changes to `backend/app/main.py`; it is framework-owned backend shell
  infrastructure and must remain stable for app startup and router mounting.
- Do not plan changes to `backend/app/models/__init__.py`; new models should live in
  their own module files and be imported directly where used.
- If the plan adds or changes persisted schema (tables, columns, indexes, foreign keys),
  include exactly one Alembic migration under `backend/alembic/versions/` and set
  `requires_migration=true`.
- Use the current Alembic revision chain from the repo map when planning migrations.
  New migrations must extend the current head; never start a second root migration.
- Keep Alembic `revision` identifiers short and stable: 32 characters maximum.
- When an app currently has no features, prefer a thin vertical slice that becomes
  observable through an existing API or UI over backend-only scaffolding.
- Treat `frontend/src/App.tsx` and `frontend/src/App.css` as protected desktop shell
  infrastructure. Do not modify them unless the request explicitly asks to change the
  desktop shell, launcher, menu bar, or window manager itself.
- Treat the desktop's system windows and onboarding surfaces as platform capabilities, not
  disposable UI. Preserve Chat, Cost, Settings, Health, Timeline, Purpose, Tasks,
  Database, and Inceptions unless the request explicitly targets those platform surfaces.
- Product apps must be implemented under `frontend/src/apps/<app-slug>/` and expose a default
  component from `frontend/src/apps/<app-slug>/index.ts` or `index.tsx`.
- Those app modules are mounted inside the existing desktop window system via
  `frontend/src/components/AppViewer.tsx`; do not replace the desktop shell.
- The desktop app registry lives in `frontend/src/apps/registry.tsx`. Do not plan or recreate
  legacy registration files such as `frontend/src/config/apps.ts` unless the repository map
  explicitly shows that file exists.
- The repository map is the source of truth for existing frontend app module roots. Reuse the
  exact path it reports for an app and never create a sibling module that differs only by case,
  camelCase, spacing, or hyphenation.
- The repository map is also the source of truth for notable static assets under
  `frontend/public/`. Reuse those exact public asset paths and do not silently move or duplicate
  them into app-module folders unless the change explicitly rewires all references.
- When registering a desktop app, keep its frontend module key stable by using a slug derived
  from the app name (for example `Example App` -> `example-app`).
- If the repository map reports a path conflict for a frontend app root, plan a consolidation or
  stabilization slice before deepening that app further.
- If a product app already has a mounted frontend surface, preserve its backend route contract
  and prefer returning a safe empty state over leaving the UI with HTTP 404 / missing endpoint
  failures.

Be precise. Every file that needs to change must be listed. Think step by step."""

_FRONTEND_APPS_PREFIX = "frontend/src/apps/"
_LEGACY_APP_REGISTRY_PATH = "frontend/src/config/apps.ts"
_CANONICAL_APP_REGISTRY_PATH = "frontend/src/apps/registry.tsx"


def _iter_repo_paths(node: FileNode | None, prefix: str = "") -> set[str]:
    """Flatten repo-map file nodes into a searchable set of relative paths."""
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
    paths = _iter_repo_paths(repo_map.tree)
    return relative_path in paths


def _normalize_frontend_change_path(path: str, repo_map: RepoMap | None) -> str:
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


def _merge_change(existing: FileChange, new_change: FileChange) -> FileChange:
    """Collapse duplicate plan entries after path normalization."""
    descriptions = OrderedDict.fromkeys(
        description
        for description in [existing.description, new_change.description]
        if description
    )
    description = " ".join(descriptions)
    action = existing.action
    if existing.action != new_change.action:
        action = "modify" if "modify" in {existing.action, new_change.action} else existing.action
    layer = existing.layer or new_change.layer
    return existing.model_copy(
        update={
            "action": action,
            "description": description,
            "layer": layer,
        }
    )


def _sanitize_plan(plan: EvolutionPlan, repo_map: RepoMap | None) -> EvolutionPlan:
    """Normalize plan paths so downstream agents target the actual repo layout."""
    deduped: OrderedDict[str, FileChange] = OrderedDict()

    for change in plan.changes:
        normalized_path = _normalize_frontend_change_path(change.file_path, repo_map)
        normalized_change = change.model_copy(update={"file_path": normalized_path})
        if normalized_path in deduped:
            deduped[normalized_path] = _merge_change(deduped[normalized_path], normalized_change)
        else:
            deduped[normalized_path] = normalized_change

    return plan.model_copy(update={"changes": list(deduped.values())})


class LeaderAgent(BaseAgent):
    """Interprets user requests and produces structured evolution plans."""

    def __init__(
        self,
        provider: BaseLLMProvider,
        purpose: Purpose | None = None,
        framework_invariants: FrameworkInvariants | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.provider = provider
        self.purpose = purpose
        self.framework_invariants = framework_invariants

    @property
    def name(self) -> str:
        return "leader"

    async def _execute(self, ctx: EvolutionContext) -> EvolutionContext:
        """Analyze the user request and produce an evolution plan."""
        # Build the user prompt with framework invariants, purpose, and repo context
        framework_context = ""
        if self.framework_invariants:
            framework_context = f"\n\n{self.framework_invariants.to_prompt_context()}"

        purpose_context = ""
        if self.purpose:
            purpose_context = f"\n\n{self.purpose.to_prompt_context()}"

        repo_context = ""
        if ctx.repo_map:
            repo_context = f"\n\n## Current Repository State\n{ctx.repo_map.to_context_string()}"

        skills_context = ""
        if ctx.available_skills:
            skill_lines = "\n".join(skill.to_prompt_line() for skill in ctx.available_skills)
            skills_context = f"\n\n## Runtime Skills Available\n{skill_lines}"

        user_prompt = (
            f"## User Request\n{ctx.request.user_request}"
            f"{framework_context}"
            f"{purpose_context}"
            f"{skills_context}"
            f"{repo_context}"
        )

        # Call LLM for structured plan generation (use fast model — planning doesn't need Sonnet)
        plan = await self.provider.generate_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=EvolutionPlan,
            model_override="fast",
        )
        plan = _sanitize_plan(plan, ctx.repo_map)

        self.logger.info(
            "plan.generated",
            summary=plan.summary,
            num_changes=len(plan.changes),
            risk_level=plan.risk_level,
        )

        return ctx.model_copy(
            update={
                "plan": plan,
                "status": EvolutionStatus.ANALYZING,
            }
        )
