"""AWS CodePipeline trigger — starts the CI/CD pipeline after a successful git push."""

import boto3
import structlog

from engine.config import EngineSettings, settings

logger = structlog.get_logger()


class PipelineTrigger:
    """Triggers AWS CodePipeline to deploy validated code changes."""

    def __init__(self, config: EngineSettings | None = None) -> None:
        self.config = config or settings
        self.client = boto3.client("codepipeline", region_name=self.config.aws_region)

    async def trigger(self, pipeline_name: str | None = None) -> str:
        """Start a pipeline execution and return the execution ID.

        In most cases, the pipeline is triggered automatically by the git push
        (via webhook or polling). This method provides a manual trigger fallback.
        """
        name = pipeline_name or self.config.pipeline_name
        if not name:
            logger.info("pipeline.skip", reason="No pipeline name configured")
            return ""

        try:
            response = self.client.start_pipeline_execution(name=name)
            execution_id = response.get("pipelineExecutionId", "")
            logger.info(
                "pipeline.triggered",
                pipeline=name,
                execution_id=execution_id,
            )
            return execution_id

        except Exception as exc:
            logger.error("pipeline.trigger_failed", pipeline=name, error=str(exc))
            return ""
