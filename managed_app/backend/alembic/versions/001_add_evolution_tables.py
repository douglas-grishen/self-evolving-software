"""Add evolution tracking tables.

Revision ID: 001_evolution
Revises: (initial)
Create Date: 2026-03-16

Creates tables for:
  - evolution_events: lifecycle records for each evolution cycle
  - inceptions: directives that modify the system's Purpose
  - purposes: versioned Purpose snapshots
"""

revision = "001_evolution"
down_revision = None
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def upgrade() -> None:
    # --- evolution_events ---
    op.create_table(
        "evolution_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(36), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("user_request", sa.Text(), nullable=False),
        sa.Column("plan_summary", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(10), nullable=True),
        sa.Column("validation_passed", sa.Boolean(), nullable=True),
        sa.Column("deployment_success", sa.Boolean(), nullable=True),
        sa.Column("commit_sha", sa.String(40), nullable=True),
        sa.Column("branch", sa.String(200), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("events_json", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_evolution_events_request_id", "evolution_events", ["request_id"])
    op.create_index("ix_evolution_events_status", "evolution_events", ["status"])
    op.create_index("ix_evolution_events_created_at", "evolution_events", ["created_at"])

    # --- inceptions ---
    op.create_table(
        "inceptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("directive", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("previous_purpose_version", sa.Integer(), nullable=True),
        sa.Column("new_purpose_version", sa.Integer(), nullable=True),
        sa.Column("changes_summary", sa.Text(), nullable=True),
    )
    op.create_index("ix_inceptions_status", "inceptions", ["status"])
    op.create_index("ix_inceptions_submitted_at", "inceptions", ["submitted_at"])

    # --- purposes ---
    op.create_table(
        "purposes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, unique=True),
        sa.Column("content_yaml", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("inception_id", sa.String(36), sa.ForeignKey("inceptions.id"), nullable=True),
    )
    op.create_index("ix_purposes_version", "purposes", ["version"])


def downgrade() -> None:
    op.drop_table("purposes")
    op.drop_table("inceptions")
    op.drop_table("evolution_events")
