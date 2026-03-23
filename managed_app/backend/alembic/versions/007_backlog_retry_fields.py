"""Add retry metadata to proactive backlog items.

Revision ID: 007_backlog_retry_fields
Revises: 006_evolution_backlog
Create Date: 2026-03-23
"""

from alembic import op
import sqlalchemy as sa

revision = "007_backlog_retry_fields"
down_revision = "006_evolution_backlog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evolution_backlog_items",
        sa.Column("failure_streak", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "evolution_backlog_items",
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "evolution_backlog_items",
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("evolution_backlog_items", "last_attempted_at")
    op.drop_column("evolution_backlog_items", "retry_after")
    op.drop_column("evolution_backlog_items", "failure_streak")
