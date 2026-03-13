#!/usr/bin/env python3
"""CDK App — entrypoint for deploying the Self-Evolving Software infrastructure.

MAPE-K Architecture:
    The system deploys two isolated subsystems on a single EC2 instance:
    - Managed System:     web app (React + FastAPI + PostgreSQL)
    - Autonomic Manager:  AI engine (Leader, DataManager, Generator, Validator)

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

import aws_cdk as cdk

from stacks.ec2_stack import Ec2Stack
from stacks.network_stack import NetworkStack
from stacks.pipeline_stack import PipelineStack

app = cdk.App()

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
network = NetworkStack(app, "NetworkStack", env=env)

# 2. EC2 instance — hosts both Managed System and Autonomic Manager
ec2_instance = Ec2Stack(
    app,
    "Ec2Stack",
    vpc=network.vpc,
    ec2_sg=network.ec2_sg,
    env=env,
)

# 3. CI/CD Pipeline — deploys both subsystems to EC2 via CodeDeploy
pipeline = PipelineStack(
    app,
    "PipelineStack",
    codedeploy_app=ec2_instance.codedeploy_app,
    deployment_group=ec2_instance.deployment_group,
    env=env,
)

app.synth()
