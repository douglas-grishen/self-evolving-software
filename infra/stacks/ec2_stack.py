"""EC2 Stack — single instance hosting both planes via Docker Compose.

Architecture (MAPE-K self-adaptive system):
┌─────────────────────────────────────────────────────────────────┐
│  EC2 Instance (t3.medium, Amazon Linux 2023)                    │
│                                                                 │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  OPERATIONAL PLANE (operational-plane Docker network)     │ │
│  │  postgres (EBS /mnt/pgdata) + backend + frontend (:80)    │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  EVOLUTION PLANE (evolution-plane Docker network)         │ │
│  │  engine: Leader → DataManager → Generator → Validator     │ │
│  │  Interfaces: file system (ro), Docker socket, Git, AWS    │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘

Resources:
- EC2 instance with Elastic IP for stable public address
- EBS GP3 volume for PostgreSQL data persistence (/mnt/pgdata)
- IAM role: SSM (access), CodeDeploy (CI/CD), Bedrock (LLM), CodeBuild (sandbox)
- CodeDeploy application + deployment group for automated deployments
- User data: Docker + Compose + CodeDeploy agent, EBS mount, instance env bootstrap
"""

import shlex

import aws_cdk as cdk
from aws_cdk import RemovalPolicy, Stack, Tags
from aws_cdk import aws_codedeploy as codedeploy
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from constructs import Construct

from instance_overlay import InstanceOverlay


def _env_line(key: str, value: str) -> str:
    """Render one shell-safe dotenv assignment."""
    return f"{key}={shlex.quote(value)}"


class Ec2Stack(Stack):
    """Deploys a single EC2 instance running the full self-evolving software stack."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.Vpc,
        ec2_sg: ec2.SecurityGroup,
        instance_overlay: InstanceOverlay,
        project_name: str = "self-evolving-software",
        instance_type_str: str = "t3.medium",
        ebs_data_size_gb: int = 30,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        region = Stack.of(self).region

        # ── IAM Role ────────────────────────────────────────────────────────
        role = iam.Role(
            self,
            "InstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                # SSM Session Manager (SSH-less access)
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                ),
                # CodeDeploy agent
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonEC2RoleforAWSCodeDeploy"
                ),
            ],
        )

        # Amazon Bedrock — for the evolving engine LLM calls
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=["*"],
            )
        )

        # CodeBuild — for the validator sandbox (cloud mode)
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "codebuild:StartBuild",
                    "codebuild:BatchGetBuilds",
                    "codebuild:StopBuild",
                ],
                resources=["*"],
            )
        )

        # CodePipeline — for the deployer to trigger pipelines
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["codepipeline:StartPipelineExecution"],
                resources=["*"],
            )
        )

        # KMS + S3 — CodeDeploy needs to decrypt and download CodePipeline artifacts
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "kms:Decrypt",
                    "kms:DescribeKey",
                    "kms:GenerateDataKey",
                ],
                resources=["*"],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3:GetObject",
                    "s3:GetObjectVersion",
                    "s3:GetBucketVersioning",
                ],
                resources=["*"],
            )
        )

        # ── User Data Script ────────────────────────────────────────────────
        framework_root = instance_overlay.framework_root
        bundle_root = instance_overlay.bundle_root
        instance_name = f"{project_name}-{instance_overlay.instance_key}"
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -euxo pipefail",
            "",
            "# --- System packages ---",
            "# Note: curl-minimal conflicts with curl on AL2023; use --allowerasing to replace it",
            "dnf update -y",
            "dnf install -y docker git ruby wget --allowerasing",
            "",
            "# --- Docker ---",
            "systemctl enable docker",
            "systemctl start docker",
            "usermod -aG docker ec2-user",
            "",
            "# --- Docker Compose v2 plugin ---",
            "DOCKER_CONFIG=/usr/local/lib/docker",
            "mkdir -p $DOCKER_CONFIG/cli-plugins",
            'ARCH=$(uname -m)',
            'curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${ARCH}" '
            "-o $DOCKER_CONFIG/cli-plugins/docker-compose",
            "chmod +x $DOCKER_CONFIG/cli-plugins/docker-compose",
            "ln -sf $DOCKER_CONFIG/cli-plugins/docker-compose /usr/local/bin/docker-compose",
            "",
            "# --- Docker Buildx plugin (required by Compose v5+) ---",
            'BUILDX_ARCH=$([ "$ARCH" = "x86_64" ] && echo amd64 || echo arm64)',
            'curl -SL "https://github.com/docker/buildx/releases/download/v0.21.2/buildx-v0.21.2.linux-${BUILDX_ARCH}" '
            "-o $DOCKER_CONFIG/cli-plugins/docker-buildx",
            "chmod +x $DOCKER_CONFIG/cli-plugins/docker-buildx",
            "",
            "# --- CodeDeploy agent ---",
            "cd /tmp",
            f"wget https://aws-codedeploy-{region}.s3.{region}.amazonaws.com/latest/install",
            "chmod +x ./install",
            "./install auto",
            "",
            "# --- Mount EBS volume for PostgreSQL data ---",
            'DEVICE="/dev/xvdf"',
            'MOUNT_POINT="/mnt/pgdata"',
            "",
            "# Wait for device to appear",
            'while [ ! -e "$DEVICE" ]; do sleep 1; done',
            "",
            "# Only format if no filesystem exists (preserves data on reboot)",
            'if ! blkid "$DEVICE"; then',
            '    mkfs.ext4 "$DEVICE"',
            "fi",
            "",
            'mkdir -p "$MOUNT_POINT"',
            'mount "$DEVICE" "$MOUNT_POINT"',
            "",
            "# Persist mount across reboots",
            'if ! grep -q "$MOUNT_POINT" /etc/fstab; then',
            '    echo "$DEVICE $MOUNT_POINT ext4 defaults,nofail 0 2" >> /etc/fstab',
            "fi",
            "",
            "# Create subdirectory for PostgreSQL (avoids lost+found conflict)",
            'mkdir -p "$MOUNT_POINT/data"',
            "# Set ownership for PostgreSQL container (uid 999 = postgres in alpine image)",
            'chown -R 999:999 "$MOUNT_POINT/data"',
            "",
            "# --- Prepare framework directories ---",
            f"FRAMEWORK_ROOT={shlex.quote(framework_root)}",
            f"BUNDLE_ROOT={shlex.quote(bundle_root)}",
            f"EVOLVED_APP_ROOT={shlex.quote(instance_overlay.evolved_app_root)}",
            f"COMPOSE_PROJECT={shlex.quote(instance_overlay.compose_project)}",
            'mkdir -p "$(dirname "$FRAMEWORK_ROOT")" "$BUNDLE_ROOT" "$FRAMEWORK_ROOT" "$EVOLVED_APP_ROOT"',
            "",
            "# --- Create .env file ---",
            'PGPASS=$(openssl rand -base64 24 | tr -dc "a-zA-Z0-9" | head -c 24)',
            'cat > "$FRAMEWORK_ROOT/.env" << ENVEOF',
            "POSTGRES_USER=postgres",
            "POSTGRES_PASSWORD=$PGPASS",
            _env_line("POSTGRES_DB", instance_overlay.db_name),
            f"AWS_REGION={region}",
            _env_line("INSTANCE_KEY", instance_overlay.instance_key),
            _env_line("INSTANCE_OVERLAY_PATH", instance_overlay.instance_overlay_path),
            _env_line("PUBLIC_HOST", instance_overlay.public_host),
            _env_line("FRAMEWORK_ROOT", framework_root),
            _env_line("EVOLVED_APP_ROOT", instance_overlay.evolved_app_root),
            _env_line("INSTANCE_STATE_ROOT", instance_overlay.instance_state_root),
            _env_line("PURPOSE_PATH", instance_overlay.purpose_path),
            _env_line("PURPOSE_HISTORY_PATH", instance_overlay.purpose_history_path),
            _env_line("GENESIS_PATH", instance_overlay.genesis_path),
            _env_line(
                "GENESIS_SEED_PATH",
                f"{framework_root}/{instance_overlay.genesis_repo_path}",
            ),
            _env_line(
                "RUNTIME_CONTRACTS_SEED_PATH",
                f"{framework_root}/{instance_overlay.contracts_repo_path}",
            ),
            _env_line("RUNTIME_CONTRACTS_PATH", instance_overlay.runtime_contracts_path),
            _env_line("USAGE_STATE_PATH", instance_overlay.usage_state_path),
            _env_line("COMPOSE_PROJECT", instance_overlay.compose_project),
            _env_line("COMPOSE_FILE", "docker-compose.prod.yml"),
            _env_line("APP_APP_NAME", instance_overlay.app_name),
            "ENVEOF",
            "",
            "# Save a backup copy for CodeDeploy to use",
            'cp "$FRAMEWORK_ROOT/.env" /home/ec2-user/.env',
            "chown ec2-user:ec2-user /home/ec2-user/.env",
            "",
            "# --- Prepare instance-local state only ---",
            'set -a',
            '. "$FRAMEWORK_ROOT/.env"',
            'set +a',
            'mkdir -p "$EVOLVED_APP_ROOT" "$INSTANCE_STATE_ROOT" "$PURPOSE_HISTORY_PATH"',
            'rm -f "$FRAMEWORK_ROOT/purpose.yaml"',
            'rm -f "$EVOLVED_APP_ROOT/.engine-state/purpose.yaml"',
            'rm -f "$INSTANCE_STATE_ROOT/purpose.yaml"',
            'echo "Bootstrap complete. Waiting for CodeDeploy to deliver the selected GitHub source bundle."',
        )

        # ── EC2 Instance ────────────────────────────────────────────────────
        self.instance = ec2.Instance(
            self,
            "EvolvingInstance",
            instance_type=ec2.InstanceType(instance_type_str),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=ec2_sg,
            role=role,
            user_data=user_data,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        20, volume_type=ec2.EbsDeviceVolumeType.GP3
                    ),
                )
            ],
        )

        Tags.of(self.instance).add("Name", instance_name)

        # ── EBS Volume for PostgreSQL Data ──────────────────────────────────
        pg_volume = ec2.Volume(
            self,
            "PgDataVolume",
            availability_zone=self.instance.instance_availability_zone,
            size=cdk.Size.gibibytes(ebs_data_size_gb),
            volume_type=ec2.EbsDeviceVolumeType.GP3,
            encrypted=True,
            removal_policy=RemovalPolicy.SNAPSHOT,
        )

        Tags.of(pg_volume).add("Name", f"{instance_name}-pgdata")

        ec2.CfnVolumeAttachment(
            self,
            "PgVolumeAttachment",
            device="/dev/xvdf",
            instance_id=self.instance.instance_id,
            volume_id=pg_volume.volume_id,
        )

        # ── Elastic IP ──────────────────────────────────────────────────────
        self.eip = ec2.CfnEIP(self, "EvolvingEIP")

        # Use allocation_id (stable) instead of the deprecated eip (public IP)
        ec2.CfnEIPAssociation(
            self,
            "EipAssociation",
            instance_id=self.instance.instance_id,
            allocation_id=self.eip.attr_allocation_id,
        )

        # ── CodeDeploy ──────────────────────────────────────────────────────
        self.codedeploy_app = codedeploy.ServerApplication(
            self,
            "CodeDeployApp",
            application_name=instance_overlay.codedeploy_app_name,
        )

        self.deployment_group = codedeploy.ServerDeploymentGroup(
            self,
            "DeploymentGroup",
            application=self.codedeploy_app,
            deployment_group_name=instance_overlay.deployment_group_name,
            ec2_instance_tags=codedeploy.InstanceTagSet(
                {"Name": [instance_name]},
            ),
            install_agent=True,
            deployment_config=codedeploy.ServerDeploymentConfig.ALL_AT_ONCE,
        )

        # ── Outputs ─────────────────────────────────────────────────────────
        aws_profile = self.node.try_get_context("aws_profile") or "default"

        cdk.CfnOutput(self, "PublicIP", value=self.eip.attr_public_ip)
        cdk.CfnOutput(self, "InstanceId", value=self.instance.instance_id)
        cdk.CfnOutput(
            self,
            "SSMConnect",
            value=(
                f"aws ssm start-session --target {self.instance.instance_id} "
                f"--profile {aws_profile} --region {region}"
            ),
        )
