"""Add runtime skills registry.

Revision ID: 009_runtime_skills
Revises: 008_system_notifications
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "009_runtime_skills"
down_revision = "008_system_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(120), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("scope", sa.String(40), nullable=False, server_default="engine_and_apps"),
        sa.Column("executor_kind", sa.String(40), nullable=False, server_default="local"),
        sa.Column("config_json", postgresql.JSONB(), nullable=True),
        sa.Column("permissions_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_skills_key", "skills", ["key"], unique=True)
    op.create_index("ix_skills_status", "skills", ["status"])

    op.execute(
        """
        INSERT INTO skills (
            id, key, name, description, status, scope, executor_kind,
            config_json, permissions_json, created_at, updated_at
        ) VALUES (
            gen_random_uuid()::text,
            'web-browser',
            'Web Browser',
            'Structured browser automation over Playwright with auditable actions.',
            'active',
            'engine_and_apps',
            'local',
            '{"browser":"chromium"}'::jsonb,
            '{"requires_enabled_setting":"skill_browser_enabled"}'::jsonb,
            NOW(),
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO skills (
            id, key, name, description, status, scope, executor_kind,
            config_json, permissions_json, created_at, updated_at
        ) VALUES (
            gen_random_uuid()::text,
            'send-email',
            'Send Email',
            'Transactional email delivery through Resend.',
            'active',
            'engine_and_apps',
            'local',
            '{"provider":"resend"}'::jsonb,
            '{"requires_enabled_setting":"skill_email_enabled","requires_secret_setting":"skill_email_resend_api_key"}'::jsonb,
            NOW(),
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_skills_status", "skills")
    op.drop_index("ix_skills_key", "skills")
    op.drop_table("skills")
