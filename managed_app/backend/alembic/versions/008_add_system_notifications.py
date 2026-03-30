"""Add persistent system notifications.

Revision ID: 008_system_notifications
Revises: 007_backlog_retry_fields
Create Date: 2026-03-27
"""

from alembic import op
import sqlalchemy as sa

revision = "008_system_notifications"
down_revision = "007_backlog_retry_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="system"),
        sa.Column("kind", sa.String(40), nullable=False, server_default="runtime_blocker"),
        sa.Column("severity", sa.String(20), nullable=False, server_default="high"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("message_hash", sa.String(64), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("update_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_system_notifications_source",
        "system_notifications",
        ["source"],
    )
    op.create_index(
        "ix_system_notifications_kind",
        "system_notifications",
        ["kind"],
    )
    op.create_index(
        "ix_system_notifications_severity",
        "system_notifications",
        ["severity"],
    )
    op.create_index(
        "ix_system_notifications_acknowledged",
        "system_notifications",
        ["acknowledged"],
    )
    op.create_index(
        "ix_system_notifications_message_hash",
        "system_notifications",
        ["message_hash"],
    )
    op.create_index(
        "ix_system_notifications_updated_at",
        "system_notifications",
        ["updated_at"],
    )
    op.create_index(
        "ix_system_notifications_hash_updated",
        "system_notifications",
        ["message_hash", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_system_notifications_hash_updated", "system_notifications")
    op.drop_index("ix_system_notifications_updated_at", "system_notifications")
    op.drop_index("ix_system_notifications_message_hash", "system_notifications")
    op.drop_index("ix_system_notifications_acknowledged", "system_notifications")
    op.drop_index("ix_system_notifications_severity", "system_notifications")
    op.drop_index("ix_system_notifications_kind", "system_notifications")
    op.drop_index("ix_system_notifications_source", "system_notifications")
    op.drop_table("system_notifications")
