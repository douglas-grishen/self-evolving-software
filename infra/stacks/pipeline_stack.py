"""Pipeline Stack — CodePipeline with CodeDeploy to EC2.

Pipeline stages:
1. Source: GitHub repository (webhook trigger on push to main branch)
2. Deploy: CodeDeploy pushes source to EC2, runs hook scripts
   (stop → build images → start services)

No ECR or CodeBuild needed — images are built directly on the EC2 instance.

Required CDK context (set via --context or env vars, never hardcoded):
  github_owner      Your GitHub username or organization
  github_repo       Repository name (default: self-evolving-software)
  github_branch     Branch to watch (default: main)
  connection_arn    AWS CodeConnections ARN for GitHub access

How to create the CodeConnections ARN:
  1. AWS Console → CodePipeline → Settings → Connections → Create connection
  2. Choose GitHub, authorize the app, copy the ARN
  3. Pass it via: cdk deploy --context connection_arn=arn:aws:...
"""

import aws_cdk as cdk
from aws_cdk import Stack
from aws_cdk import aws_codedeploy as codedeploy
from aws_cdk import aws_codepipeline as codepipeline
from aws_cdk import aws_codepipeline_actions as cpactions
from constructs import Construct

from instance_overlay import InstanceOverlay

_OFFICIAL_GITHUB_OWNER = "douglas-grishen"


class PipelineStack(Stack):
    """Creates the CI/CD pipeline that deploys code to the EC2 instance."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        codedeploy_app: codedeploy.ServerApplication,
        deployment_group: codedeploy.ServerDeploymentGroup,
        instance_overlay: InstanceOverlay,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Deployment context (personal values — never hardcoded) ───────────
        github_owner = self.node.try_get_context("github_owner") or _OFFICIAL_GITHUB_OWNER
        github_repo = self.node.try_get_context("github_repo") or "self-evolving-software"
        github_branch = self.node.try_get_context("github_branch") or "main"
        connection_arn = self.node.try_get_context("connection_arn")

        if not connection_arn:
            raise ValueError(
                "CDK context 'connection_arn' is required. "
                "Create a GitHub connection in AWS Console → CodePipeline → Settings → Connections, "
                "then pass it with: cdk deploy --context connection_arn=arn:aws:codeconnections:..."
            )

        # ── Source stage — GitHub via AWS CodeConnections ────────────────────
        source_output = codepipeline.Artifact("SourceOutput")
        source_action = cpactions.CodeStarConnectionsSourceAction(
            action_name="GitHub_Source",
            owner=github_owner,
            repo=github_repo,
            branch=github_branch,
            output=source_output,
            connection_arn=connection_arn,
        )

        # ── Deploy stage — CodeDeploy to EC2 ─────────────────────────────────
        deploy_action = cpactions.CodeDeployServerDeployAction(
            action_name="Deploy_to_EC2",
            deployment_group=deployment_group,
            input=source_output,
        )

        # ── Assemble the pipeline ────────────────────────────────────────────
        self.pipeline = codepipeline.Pipeline(
            self,
            "EvolvingPipeline",
            pipeline_name=instance_overlay.pipeline_name,
            pipeline_type=codepipeline.PipelineType.V2,
            stages=[
                codepipeline.StageProps(
                    stage_name="Source",
                    actions=[source_action],
                ),
                codepipeline.StageProps(
                    stage_name="Deploy",
                    actions=[deploy_action],
                ),
            ],
        )

        # ── Outputs ──────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "PipelineName",
            value=self.pipeline.pipeline_name,
        )
        cdk.CfnOutput(
            self,
            "PipelineConsoleUrl",
            value=(
                f"https://{self.region}.console.aws.amazon.com/codesuite/codepipeline/"
                f"pipelines/{self.pipeline.pipeline_name}/view"
            ),
        )
