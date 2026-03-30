#!/usr/bin/env python3
"""CDK App — entrypoint for deploying the Self-Evolving Software infrastructure.

MAPE-K Architecture:
    The system deploys two isolated planes on a single EC2 instance:
    - Operational Plane: web app (React + FastAPI + PostgreSQL)
    - Evolution Plane:   AI engine (Leader, DataManager, Generator, Validator)

Stack dependency graph:
    NetworkStack           — VPC, subnets, security groups
        └── Ec2Stack       — EC2 instance hosting both subsystems
            └── PipelineStack  — CI/CD: GitHub → CodeDeploy → EC2

Deployment (uses the drcoder AWS CLI profile):
    make cdk-bootstrap   # first time only — sets up CDK Toolkit in your account
    make cdk-deploy      # deploys all three stacks

Or directly:
    cd infra
    cdk bootstrap --profile drcoder
    cdk deploy --all --profile drcoder
"""

import os
from pathlib import Path

import aws_cdk as cdk

from instance_overlay import load_instance_overlay
from stacks.ec2_stack import Ec2Stack
from stacks.network_stack import NetworkStack
from stacks.pipeline_stack import PipelineStack

app = cdk.App()
repo_root = Path(__file__).resolve().parents[1]
instance_key = (
    app.node.try_get_context("instance_key")
    or os.environ.get("INSTANCE_KEY")
    or "base"
)
project_name = app.node.try_get_context("project_name") or "self-evolving-software"
instance_overlay = load_instance_overlay(repo_root, instance_key)
stack_suffix = "".join(part.capitalize() for part in instance_key.split("-"))

# CDK_DEFAULT_ACCOUNT and CDK_DEFAULT_REGION are automatically populated by the
# CDK CLI when --profile is passed. They resolve to the account/region configured
# in the drcoder profile (~/.aws/config).
# Context values from cdk.json or --context on the CLI take precedence.
env = cdk.Environment(
    account=(
        os.environ.get("CDK_DEFAULT_ACCOUNT")
        or app.node.try_get_context("account")
        or None
    ),
    region=(
        os.environ.get("CDK_DEFAULT_REGION")
        or app.node.try_get_context("region")
        or "us-east-1"
    ),
)

# 1. Network foundation (VPC, public subnet, security group)
network = NetworkStack(
    app,
    f"NetworkStack{stack_suffix}",
    stack_name=f"{project_name}-{instance_key}-network",
    env=env,
)

# 2. EC2 instance — hosts both Operational and Evolution planes
ec2_instance = Ec2Stack(
    app,
    f"Ec2Stack{stack_suffix}",
    vpc=network.vpc,
    ec2_sg=network.ec2_sg,
    instance_overlay=instance_overlay,
    project_name=project_name,
    stack_name=f"{project_name}-{instance_key}-compute",
    env=env,
)

# 3. CI/CD Pipeline — deploys both planes to EC2 via CodeDeploy
pipeline = PipelineStack(
    app,
    f"PipelineStack{stack_suffix}",
    codedeploy_app=ec2_instance.codedeploy_app,
    deployment_group=ec2_instance.deployment_group,
    instance_overlay=instance_overlay,
    stack_name=f"{project_name}-{instance_key}-pipeline",
    env=env,
)

app.synth()
