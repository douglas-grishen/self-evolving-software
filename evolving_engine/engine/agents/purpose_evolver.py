"""PurposeEvolver — processes Inceptions to evolve the system's Purpose.

When an Inception arrives, the PurposeEvolver:
  1. Analyzes the inception directive against the current Purpose
  2. Uses the LLM to reason about what should change
  3. Produces a new Purpose with an incremented version
  4. Archives the old Purpose to purpose_history/
  5. Saves the new Purpose to purpose.yaml

Safety rails:
  - Rejects inceptions that would disable security constraints
  - Rejects inceptions that would remove sandbox validation requirements
  - Always preserves core safety-related evolution directives
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from engine.config import EngineSettings
from engine.models.inception import InceptionRequest, InceptionResult
from engine.models.purpose import Purpose
from engine.providers.base import BaseLLMProvider

logger = structlog.get_logger()

SYSTEM_PROMPT = """You are the Purpose Evolver of a self-evolving software system.

Your role is to analyze an Inception — a directive that seeks to change the system's
evolution direction — and produce an updated Purpose specification.

You receive:
1. The current Purpose (the system's guiding specification)
2. An Inception directive with a rationale

You must:
1. Analyze whether the inception is valid and beneficial
2. Determine which sections of the Purpose need modification
3. Produce a complete updated Purpose as a JSON object

SAFETY RAILS — you MUST reject inceptions that:
- Would disable or weaken security requirements
- Would remove the requirement for sandbox validation before deployment
- Would allow the engine to deploy untested code
- Would remove constraints on risk levels for autonomous evolutions
- Would grant the engine access to modify infrastructure code autonomously

If the inception is valid, produce the updated Purpose with all fields.
If the inception must be rejected, return the current Purpose unchanged and
explain why in the reasoning.

Output a JSON object with these fields:
- accepted: boolean (whether the inception was accepted)
- reasoning: string (explanation of what changed and why, or why it was rejected)
- purpose: object (the full updated Purpose — same structure as input)"""


class PurposeEvolver:
    """Processes Inceptions to evolve the system's Purpose."""

    def __init__(self, provider: BaseLLMProvider, config: EngineSettings) -> None:
        self.provider = provider
        self.config = config

    async def evolve(
        self, current_purpose: Purpose, inception: InceptionRequest
    ) -> tuple[Purpose, InceptionResult]:
        """Analyze an inception and produce an updated Purpose.

        Returns:
            A tuple of (new_purpose, inception_result).
            If the inception is rejected, new_purpose == current_purpose.
        """
        logger.info(
            "purpose_evolver.start",
            inception_id=inception.id,
            directive=inception.directive[:100],
        )

        user_prompt = (
            f"## Current Purpose\n```yaml\n{current_purpose.to_yaml_string()}\n```\n\n"
            f"## Inception Directive\n{inception.directive}\n\n"
            f"## Rationale\n{inception.rationale or 'No rationale provided.'}"
        )

        response = await self.provider.generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        # Parse the LLM response to extract the updated purpose
        new_purpose, accepted, reasoning = self._parse_response(response, current_purpose)

        if accepted:
            # Increment version and update timestamp
            new_purpose = new_purpose.model_copy(
                update={
                    "version": current_purpose.version + 1,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            # Archive old purpose and save new one
            current_purpose.archive(self.config.purpose_path, self.config.purpose_history_path)
            new_purpose.save(self.config.purpose_path)

            logger.info(
                "purpose_evolver.applied",
                inception_id=inception.id,
                old_version=current_purpose.version,
                new_version=new_purpose.version,
            )
        else:
            new_purpose = current_purpose
            logger.info(
                "purpose_evolver.rejected",
                inception_id=inception.id,
                reason=reasoning[:200],
            )

        changes_summary = current_purpose.diff_summary(new_purpose) if accepted else f"Rejected: {reasoning}"

        result = InceptionResult(
            inception_id=inception.id,
            previous_purpose_version=current_purpose.version,
            new_purpose_version=new_purpose.version,
            changes_summary=changes_summary,
        )

        return new_purpose, result

    def _parse_response(
        self, response: str, fallback: Purpose
    ) -> tuple[Purpose, bool, str]:
        """Parse the LLM response into a Purpose and acceptance status."""
        import json

        # Try to extract JSON from the response
        try:
            # Look for JSON block in the response
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])

                accepted = data.get("accepted", False)
                reasoning = data.get("reasoning", "")

                if accepted and "purpose" in data:
                    purpose_data = data["purpose"]
                    new_purpose = Purpose.model_validate(purpose_data)
                    return new_purpose, True, reasoning
                else:
                    return fallback, False, reasoning

        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("purpose_evolver.parse_error", error=str(exc))

        return fallback, False, "Failed to parse LLM response"
