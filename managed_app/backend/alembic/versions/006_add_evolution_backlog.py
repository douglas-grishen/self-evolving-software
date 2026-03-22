"""Add persisted proactive backlog items.

Revision ID: 006_evolution_backlog
Revises: 005_engine_memory
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "006_evolution_backlog"
down_revision = "005_engine_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evolution_backlog_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("purpose_version", sa.Integer(), nullable=False),
        sa.Column("task_key", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("priority", sa.String(10), nullable=False, server_default="normal"),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("task_type", sa.String(20), nullable=False, server_default="evolve"),
        sa.Column("execution_request", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "acceptance_criteria",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "depends_on",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("app_spec", postgresql.JSONB(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="planner"),
        sa.Column("last_request_id", sa.String(36), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_evolution_backlog_items_purpose_version",
        "evolution_backlog_items",
        ["purpose_version"],
    )
    op.create_index(
        "ix_evolution_backlog_items_status",
        "evolution_backlog_items",
        ["status"],
    )
    op.create_index(
        "ix_evolution_backlog_items_priority",
        "evolution_backlog_items",
        ["priority"],
    )
    op.create_index(
        "ux_evolution_backlog_items_purpose_task_key",
        "evolution_backlog_items",
        ["purpose_version", "task_key"],
        unique=True,
    )
    op.create_index(
        "ix_evolution_backlog_items_purpose_sequence",
        "evolution_backlog_items",
        ["purpose_version", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_evolution_backlog_items_purpose_sequence", "evolution_backlog_items")
    op.drop_index("ux_evolution_backlog_items_purpose_task_key", "evolution_backlog_items")
    op.drop_index("ix_evolution_backlog_items_priority", "evolution_backlog_items")
    op.drop_index("ix_evolution_backlog_items_status", "evolution_backlog_items")
    op.drop_index("ix_evolution_backlog_items_purpose_version", "evolution_backlog_items")
    op.drop_table("evolution_backlog_items")
