"""Add apps, features, and capabilities tables.

Revision ID: 002_apps
Revises: 001_evolution
Create Date: 2026-03-16

Creates tables for:
  - apps: cohesive collections of features + capabilities (desktop icons)
  - features: user-facing behaviors (belong to an app)
  - capabilities: internal system abilities (can be shared)
  - feature_capabilities: many-to-many junction
  - app_capabilities: many-to-many junction for standalone capabilities
"""

revision = "002_apps"
down_revision = "001_evolution"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def upgrade() -> None:
    # --- apps ---
    op.create_table(
        "apps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("icon", sa.String(10), nullable=False, server_default=""),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="planned"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_evolution_id", sa.String(36), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_apps_status", "apps", ["status"])

    # --- capabilities ---
    op.create_table(
        "capabilities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="planned"),
        sa.Column("is_background", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_capabilities_status", "capabilities", ["status"])

    # --- features ---
    op.create_table(
        "features",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("app_id", sa.String(36), sa.ForeignKey("apps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("user_facing_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="planned"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_features_app_id", "features", ["app_id"])
    op.create_index("ix_features_status", "features", ["status"])

    # --- feature_capabilities (junction) ---
    op.create_table(
        "feature_capabilities",
        sa.Column("feature_id", sa.String(36), sa.ForeignKey("features.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("capability_id", sa.String(36), sa.ForeignKey("capabilities.id", ondelete="CASCADE"), primary_key=True),
    )

    # --- app_capabilities (junction) ---
    op.create_table(
        "app_capabilities",
        sa.Column("app_id", sa.String(36), sa.ForeignKey("apps.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("capability_id", sa.String(36), sa.ForeignKey("capabilities.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("app_capabilities")
    op.drop_table("feature_capabilities")
    op.drop_table("features")
    op.drop_table("capabilities")
    op.drop_table("apps")
