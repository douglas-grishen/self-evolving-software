"""Add engine_memory table for inter-session lessons learned.

Seeds two critical lessons immediately so the engine stops repeating
its two most common mistakes from day one.

Revision ID: 005_engine_memory
Revises: 004_system_settings
Create Date: 2026-03-19
"""

from alembic import op
import sqlalchemy as sa

revision = "005_engine_memory"
down_revision = "004_system_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "engine_memory",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("source", sa.String(10), nullable=False, server_default="auto"),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column(
            "active", sa.Boolean, nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "times_reinforced", sa.Integer, nullable=False, server_default="0"
        ),
    )
    op.create_index("ix_engine_memory_active", "engine_memory", ["active"])
    op.create_index("ix_engine_memory_severity", "engine_memory", ["severity"])
    op.create_index("ix_engine_memory_category", "engine_memory", ["category"])

    # Seed the two known recurring critical mistakes so they are active from cycle 1.
    # times_reinforced is pre-set to reflect how many times each bug has been observed.
    op.execute(
        """
        INSERT INTO engine_memory
            (id, category, title, content, source, severity, active, times_reinforced)
        VALUES
        (
            gen_random_uuid()::text,
            'forbidden_pattern',
            'Never use ''metadata'' as a SQLAlchemy column name',
            'SQLAlchemy reserves the name ''metadata'' as a class-level attribute on '
            'all DeclarativeBase subclasses. Declaring it as a mapped_column causes '
            'an AttributeError at import time that crashes the entire backend. '
            'Use ''extra_metadata'', ''metadata_json'', or any other name instead.',
            'manual',
            'critical',
            true,
            3
        ),
        (
            gen_random_uuid()::text,
            'forbidden_pattern',
            'Never import AsyncSessionLocal from app.database — it does not exist',
            'The app.database module only exports ''get_db'' (a FastAPI Depends '
            'dependency) and ''async_session'' (the sessionmaker factory). '
            '''AsyncSessionLocal'' does not exist in this codebase and importing it '
            'causes an ImportError that crashes the backend at startup. '
            'Always use: from app.database import get_db',
            'manual',
            'critical',
            true,
            2
        )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_engine_memory_category", "engine_memory")
    op.drop_index("ix_engine_memory_severity", "engine_memory")
    op.drop_index("ix_engine_memory_active", "engine_memory")
    op.drop_table("engine_memory")
