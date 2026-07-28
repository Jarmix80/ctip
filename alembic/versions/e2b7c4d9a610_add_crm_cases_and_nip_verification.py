"""Dodaj trwałe sprawy CRM i weryfikację NIP katalogu tożsamości.

Revision ID: e2b7c4d9a610
Revises: 71c4e8a2d9f0
Create Date: 2026-07-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e2b7c4d9a610"
down_revision: str | Sequence[str] | None = "71c4e8a2d9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"


def upgrade() -> None:
    """Rozszerza katalog tożsamości i tworzy trwały rejestr spraw."""
    op.add_column(
        "bot_identity_customer",
        sa.Column("nip_enc", sa.Text()),
        schema=SCHEMA,
    )
    op.add_column(
        "bot_identity_customer",
        sa.Column("nip_hmac", sa.Text()),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_bot_identity_customer_nip_hmac",
        "bot_identity_customer",
        ["nip_hmac"],
        schema=SCHEMA,
    )
    op.add_column(
        "bot_identity_resolution",
        sa.Column("nip_failure_count", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.add_column(
        "bot_identity_resolution",
        sa.Column("nip_verified_at", sa.DateTime(timezone=True)),
        schema=SCHEMA,
    )

    op.create_table(
        "bot_identity_sms_challenge",
        sa.Column("ref", sa.Text(), primary_key=True),
        sa.Column("phone_hmac", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("conversation_ref", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )

    op.create_table(
        "crm_case",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ref", sa.Text(), nullable=False, unique=True),
        sa.Column("external_ref", sa.Text(), nullable=False),
        sa.Column("conversation_ref", sa.Text()),
        sa.Column("source_channel", sa.Text(), nullable=False),
        sa.Column("source_detail", sa.Text()),
        sa.Column("source_url", sa.Text()),
        sa.Column("queue", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="new"),
        sa.Column("priority", sa.Text(), nullable=False, server_default="normal"),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("contact_name", sa.Text(), nullable=False),
        sa.Column("contact_phone", sa.Text()),
        sa.Column("contact_email", sa.Text()),
        sa.Column("customer_ref", sa.Text()),
        sa.Column("identity_status", sa.Text()),
        sa.Column("device_label", sa.Text()),
        sa.Column("device_serial_last4", sa.Text()),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.admin_user.id", ondelete="SET NULL"),
        ),
        sa.Column("owner_name", sa.Text()),
        sa.Column(
            "declared_operator_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.admin_user.id", ondelete="SET NULL"),
        ),
        sa.Column("ms_order_ref", sa.Text()),
        sa.Column("idempotency_key_hash", sa.Text(), unique=True),
        sa.Column("source_payload", sa.JSON()),
        sa.Column("is_lab", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_claimed_at", sa.DateTime(timezone=True)),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("retained_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "queue in ('sales','service_it','accounting','contracts','meters','other')",
            name="ck_crm_case_queue",
        ),
        sa.CheckConstraint(
            "status in ('new','active','transferred','done','archived')",
            name="ck_crm_case_status",
        ),
        sa.CheckConstraint(
            "priority in ('low','normal','high')",
            name="ck_crm_case_priority",
        ),
        sa.CheckConstraint(
            "source_channel in "
            "('chat','voice','form','configurator','email','phone','manual','scenario')",
            name="ck_crm_case_source_channel",
        ),
        sa.UniqueConstraint(
            "source_channel",
            "external_ref",
            name="uq_crm_case_source_external_ref",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_crm_case_queue_status",
        "crm_case",
        ["queue", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_crm_case_is_lab_retained_until",
        "crm_case",
        ["is_lab", "retained_until"],
        schema=SCHEMA,
    )

    op.create_table(
        "crm_case_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ref", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.crm_case.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.admin_user.id", ondelete="SET NULL"),
        ),
        sa.Column("actor_name", sa.Text()),
        sa.Column("payload", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_crm_case_event_case_created",
        "crm_case_event",
        ["case_id", "created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Usuwa sprawy CRM i pola weryfikacji NIP."""
    op.drop_table("crm_case_event", schema=SCHEMA)
    op.drop_table("crm_case", schema=SCHEMA)
    op.drop_table("bot_identity_sms_challenge", schema=SCHEMA)
    op.drop_column("bot_identity_resolution", "nip_verified_at", schema=SCHEMA)
    op.drop_column("bot_identity_resolution", "nip_failure_count", schema=SCHEMA)
    op.drop_index(
        "idx_bot_identity_customer_nip_hmac",
        table_name="bot_identity_customer",
        schema=SCHEMA,
    )
    op.drop_column("bot_identity_customer", "nip_hmac", schema=SCHEMA)
    op.drop_column("bot_identity_customer", "nip_enc", schema=SCHEMA)
