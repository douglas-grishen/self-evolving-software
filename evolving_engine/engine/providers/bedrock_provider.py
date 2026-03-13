"""Amazon Bedrock LLM provider — for AWS-native deployments."""

import json

import boto3
import structlog

from engine.config import EngineSettings, settings
from engine.providers.base import BaseLLMProvider

logger = structlog.get_logger()


class BedrockProvider(BaseLLMProvider):
    """LLM provider backed by Amazon Bedrock's Converse API."""

    def __init__(self, config: EngineSettings | None = None) -> None:
        cfg = config or settings
        self.client = boto3.client("bedrock-runtime", region_name=cfg.bedrock_region)
        self.model_id = cfg.bedrock_model_id

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
    ) -> str:
        """Call Amazon Bedrock Converse API and return the text response.

        Note: boto3 is synchronous. For true async, consider aioboto3.
        This implementation is sufficient for the engine's sequential pipeline.
        """
        logger.debug(
            "bedrock.generate",
            model_id=self.model_id,
            system_len=len(system_prompt),
            user_len=len(user_prompt),
        )

        response = self.client.converse(
            modelId=self.model_id,
            system=[{"text": system_prompt}],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": user_prompt}],
                }
            ],
            inferenceConfig={"maxTokens": max_tokens},
        )

        # Extract text from converse response
        output = response.get("output", {})
        message = output.get("message", {})
        content = message.get("content", [])

        text = ""
        for block in content:
            if "text" in block:
                text += block["text"]

        usage = response.get("usage", {})
        logger.debug(
            "bedrock.response",
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
        )

        return text
