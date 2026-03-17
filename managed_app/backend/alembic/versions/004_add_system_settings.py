"""Add system_settings table.

Revision ID: 004_system_settings
Revises: 003_apps
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa

revision = "004_system_settings"
down_revision = "003_apps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(100), nullable=False, unique=True),
        sa.Column("value", sa.Text, nullable=False, server_default=""),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    sa.Index("ix_system_settings_key", "system_settings", "key")

    # Seed default settings
    op.execute("""
        INSERT INTO system_settings (id, key, value, description, updated_at) VALUES
        (gen_random_uuid()::text, 'proactive_interval_minutes', '60',
         'How often the engine runs proactive evolution (minutes). Min: 5, Max: 1440.',
         NOW()),
        (gen_random_uuid()::text, 'anthropic_api_key', '',
         'Override for the Anthropic API key. Leave blank to use ENGINE_ANTHROPIC_API_KEY env var. Requires engine restart.',
         NOW())
    """)


def downgrade() -> None:
    op.drop_table("system_settings")
