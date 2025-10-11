"""init ctip schema

Revision ID: 7615e8207b7a
Revises:
Create Date: 2025-10-11 01:09:08.359881

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7615e8207b7a"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ctip"))

    op.create_table(
        "calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ext", sa.Text(), nullable=False),
        sa.Column("number", sa.Text(), nullable=True),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("answered_by", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("disposition", sa.Text(), nullable=False, server_default="UNKNOWN"),
        sa.Column("last_state", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint("direction in ('IN','OUT')", name="calls_direction_check"),
        sa.CheckConstraint(
            "disposition in ('ANSWERED','NO_ANSWER','BUSY','FAILED','UNKNOWN')",
            name="calls_disposition_check",
        ),
        schema="ctip",
    )

    op.create_table(
        "call_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("call_id", sa.Integer(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("typ", sa.Text(), nullable=False),
        sa.Column("ext", sa.Text(), nullable=True),
        sa.Column("number", sa.Text(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.create_foreign_key(
        "call_events_call_id_fkey",
        "call_events",
        "calls",
        local_cols=["call_id"],
        remote_cols=["id"],
        source_schema="ctip",
        referent_schema="ctip",
        ondelete="CASCADE",
    )

    op.create_table(
        "sms_out",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column("dest", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="ivr"),
        sa.Column("status", sa.Text(), nullable=False, server_default="NEW"),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("call_id", sa.Integer(), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("origin", sa.Text(), nullable=False, server_default="ui"),
        sa.Column("provider_msg_id", sa.Text(), nullable=True),
        sa.Column("provider_status", sa.Text(), nullable=True),
        sa.Column("provider_error_code", sa.Text(), nullable=True),
        sa.Column("provider_error_desc", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.create_foreign_key(
        "sms_out_call_id_fkey",
        "sms_out",
        "calls",
        local_cols=["call_id"],
        remote_cols=["id"],
        source_schema="ctip",
        referent_schema="ctip",
    )

    op.create_table(
        "ivr_map",
        sa.Column("digit", sa.SmallInteger(), nullable=False),
        sa.Column("ext", sa.Text(), nullable=False),
        sa.Column("sms_text", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("digit", "ext"),
        schema="ctip",
    )

    op.create_table(
        "contact",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("number", sa.Text(), nullable=False),
        sa.Column("ext", sa.Text(), nullable=True),
        sa.Column("first_name", sa.Text(), nullable=True),
        sa.Column("last_name", sa.Text(), nullable=True),
        sa.Column("company", sa.Text(), nullable=True),
        sa.Column("nip", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False, server_default="manual"),
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
        schema="ctip",
    )

    op.create_table(
        "contact_device",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("device_name", sa.Text(), nullable=True),
        sa.Column("serial_number", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        schema="ctip",
    )
    op.create_foreign_key(
        "contact_device_contact_id_fkey",
        "contact_device",
        "contact",
        local_cols=["contact_id"],
        remote_cols=["id"],
        source_schema="ctip",
        referent_schema="ctip",
        ondelete="CASCADE",
    )

    # Indeksy i ograniczenia dodatkowe
    op.create_index("idx_calls_answered_by", "calls", ["answered_by"], schema="ctip")
    op.create_index("idx_calls_direction", "calls", ["direction"], schema="ctip")
    op.create_index("idx_calls_ext", "calls", ["ext"], schema="ctip")
    op.create_index("idx_calls_started_at", "calls", ["started_at"], schema="ctip")
    op.create_index("idx_calls_number", "calls", ["number"], schema="ctip")

    op.create_index("idx_events_call_id", "call_events", ["call_id"], schema="ctip")
    op.create_index("idx_events_ts", "call_events", ["ts"], schema="ctip")

    op.create_index("idx_sms_out_status", "sms_out", ["status"], schema="ctip")
    op.create_index("idx_sms_out_dest_created", "sms_out", ["dest", "created_at"], schema="ctip")
    op.create_index(
        "idx_sms_out_created_by",
        "sms_out",
        ["created_by", "created_at"],
        schema="ctip",
    )
    op.create_index(
        "uq_sms_out_callid_ivr",
        "sms_out",
        ["call_id"],
        unique=True,
        schema="ctip",
        postgresql_where=sa.text("source='ivr'"),
    )

    op.create_index("idx_contact_number", "contact", ["number"], schema="ctip")
    op.create_index("idx_contact_ext", "contact", ["ext"], schema="ctip")

    op.create_index("idx_ivr_map_ext", "ivr_map", ["ext"], schema="ctip")


def downgrade() -> None:
    op.drop_index("idx_ivr_map_ext", table_name="ivr_map", schema="ctip")

    op.drop_index("idx_contact_ext", table_name="contact", schema="ctip")
    op.drop_index("idx_contact_number", table_name="contact", schema="ctip")

    op.drop_index("uq_sms_out_callid_ivr", table_name="sms_out", schema="ctip")
    op.drop_index("idx_sms_out_created_by", table_name="sms_out", schema="ctip")
    op.drop_index("idx_sms_out_dest_created", table_name="sms_out", schema="ctip")
    op.drop_index("idx_sms_out_status", table_name="sms_out", schema="ctip")

    op.drop_index("idx_events_ts", table_name="call_events", schema="ctip")
    op.drop_index("idx_events_call_id", table_name="call_events", schema="ctip")

    op.drop_index("idx_calls_number", table_name="calls", schema="ctip")
    op.drop_index("idx_calls_started_at", table_name="calls", schema="ctip")
    op.drop_index("idx_calls_ext", table_name="calls", schema="ctip")
    op.drop_index("idx_calls_direction", table_name="calls", schema="ctip")
    op.drop_index("idx_calls_answered_by", table_name="calls", schema="ctip")

    op.drop_constraint("contact_device_contact_id_fkey", "contact_device", schema="ctip")
    op.drop_table("contact_device", schema="ctip")

    op.drop_table("contact", schema="ctip")

    op.drop_table("ivr_map", schema="ctip")

    op.drop_constraint("sms_out_call_id_fkey", "sms_out", schema="ctip")
    op.drop_table("sms_out", schema="ctip")

    op.drop_constraint("call_events_call_id_fkey", "call_events", schema="ctip")
    op.drop_table("call_events", schema="ctip")

    op.drop_table("calls", schema="ctip")

    op.execute(sa.text("DROP SCHEMA IF EXISTS ctip CASCADE"))
