"""add assistant chat module

Revision ID: a9f5c8e7d2b1
Revises: d7a2c9f8e041
Create Date: 2026-04-30 12:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9f5c8e7d2b1"
down_revision: str | Sequence[str] | None = "d7a2c9f8e041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"


def upgrade() -> None:
    op.create_table(
        "assistant_chat_thread",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.CheckConstraint(
            "status in ('active','archived','deleted')",
            name="assistant_chat_thread_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["ctip.admin_user.id"],
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_assistant_chat_thread_owner_last_activity",
        "assistant_chat_thread",
        ["owner_user_id", "last_activity_at"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "assistant_chat_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("openai_response_id", sa.Text(), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.CheckConstraint(
            "role in ('user','assistant','system','tool')",
            name="assistant_chat_message_role_check",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["ctip.assistant_chat_thread.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["ctip.admin_user.id"],
            ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_assistant_chat_message_thread_created",
        "assistant_chat_message",
        ["thread_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "assistant_tool_call_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="success"),
        sa.Column("tool_input", sa.JSON(), nullable=True),
        sa.Column("tool_output", sa.JSON(), nullable=True),
        sa.Column("generated_sql", sa.Text(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "tool_name in ('firebird_read','sheets_read','imap_read','ctip_schema_read')",
            name="assistant_tool_call_log_tool_name_check",
        ),
        sa.CheckConstraint(
            "status in ('success','error','blocked')",
            name="assistant_tool_call_log_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["ctip.assistant_chat_thread.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["ctip.assistant_chat_message.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["ctip.admin_user.id"],
            ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_assistant_tool_call_log_thread_started",
        "assistant_tool_call_log",
        ["thread_id", "started_at"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "assistant_change_request",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("thread_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.CheckConstraint(
            "status in ('pending','approved','rejected','executed')",
            name="assistant_change_request_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["ctip.admin_user.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["ctip.assistant_chat_thread.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["ctip.assistant_chat_message.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["ctip.admin_user.id"],
            ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_assistant_change_request_status_created",
        "assistant_change_request",
        ["status", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "idx_assistant_change_request_created_by",
        "assistant_change_request",
        ["created_by", "created_at"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "assistant_user_profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("personalization_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("preferences", sa.JSON(), nullable=True),
        sa.Column("memory_notes", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["ctip.admin_user.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_assistant_user_profile_user_id"),
        schema=SCHEMA,
    )

    op.create_table(
        "assistant_weekly_insight",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column("generated_by", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["generated_by"], ["ctip.admin_user.id"], ondelete="SET NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_assistant_weekly_insight_week_start",
        "assistant_weekly_insight",
        ["week_start"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_assistant_weekly_insight_week_start",
        table_name="assistant_weekly_insight",
        schema=SCHEMA,
    )
    op.drop_table("assistant_weekly_insight", schema=SCHEMA)

    op.drop_table("assistant_user_profile", schema=SCHEMA)

    op.drop_index(
        "idx_assistant_change_request_created_by",
        table_name="assistant_change_request",
        schema=SCHEMA,
    )
    op.drop_index(
        "idx_assistant_change_request_status_created",
        table_name="assistant_change_request",
        schema=SCHEMA,
    )
    op.drop_table("assistant_change_request", schema=SCHEMA)

    op.drop_index(
        "idx_assistant_tool_call_log_thread_started",
        table_name="assistant_tool_call_log",
        schema=SCHEMA,
    )
    op.drop_table("assistant_tool_call_log", schema=SCHEMA)

    op.drop_index(
        "idx_assistant_chat_message_thread_created",
        table_name="assistant_chat_message",
        schema=SCHEMA,
    )
    op.drop_table("assistant_chat_message", schema=SCHEMA)

    op.drop_index(
        "idx_assistant_chat_thread_owner_last_activity",
        table_name="assistant_chat_thread",
        schema=SCHEMA,
    )
    op.drop_table("assistant_chat_thread", schema=SCHEMA)
