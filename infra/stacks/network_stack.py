"""Network Stack — simplified VPC with public subnet for single EC2 deployment."""

from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class NetworkStack(Stack):
    """Creates the foundational networking infrastructure.

    Resources:
    - VPC with 1 Availability Zone
    - Public subnet only (no private subnets, no NAT Gateway)
    - Single security group for the EC2 instance
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # VPC with public subnet only — no NAT Gateway (cost savings)
        self.vpc = ec2.Vpc(
            self,
            "EvolvingVpc",
            max_azs=1,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
            ],
        )

        # Security group for the EC2 instance
        self.ec2_sg = ec2.SecurityGroup(
            self,
            "Ec2SG",
            vpc=self.vpc,
            description="Security group for the self-evolving software EC2 instance",
            allow_all_outbound=True,
        )

        # HTTP (frontend via Nginx)
        self.ec2_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(80),
            "Allow HTTP traffic",
        )

        # HTTPS (for when TLS is configured)
        self.ec2_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "Allow HTTPS traffic",
        )

        # SSH — can be restricted via CDK context: -c ssh_cidr=203.0.113.0/24
        ssh_cidr = self.node.try_get_context("ssh_cidr") or "0.0.0.0/0"
        self.ec2_sg.add_ingress_rule(
            ec2.Peer.ipv4(ssh_cidr),
            ec2.Port.tcp(22),
            "Allow SSH access (prefer SSM Session Manager instead)",
        )
