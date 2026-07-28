"""Dodaj wspólny katalog tożsamości klientów dla botów.

Revision ID: 71c4e8a2d9f0
Revises: d6f1a8c3e740
Create Date: 2026-07-24 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "71c4e8a2d9f0"
down_revision: str | Sequence[str] | None = "d6f1a8c3e740"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"


def upgrade() -> None:
    op.create_table(
        "bot_identity_customer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_ref", sa.Text(), nullable=False, unique=True),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen_sync_id", sa.Text()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "bot_identity_subject",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_ref", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_revision", sa.Text()),
        sa.Column("last_seen_sync_id", sa.Text()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "external_ref", name="uq_bot_identity_subject_source_ref"),
        schema=SCHEMA,
    )
    op.create_table(
        "bot_identity_sync_run",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "source",
            sa.Text(),
            nullable=False,
            server_default="firebird_mobile_account",
        ),
        sa.Column("source_revision", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("accounts_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("customers_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("devices_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_phones", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        schema=SCHEMA,
    )
    op.create_table(
        "bot_identity_phone",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "subject_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.bot_identity_subject.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phone_enc", sa.Text(), nullable=False),
        sa.Column("phone_hmac", sa.Text(), nullable=False),
        sa.Column("phone_last4", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen_sync_id", sa.Text()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("subject_id", "phone_hmac", name="uq_bot_identity_phone_subject"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_bot_identity_phone_hmac",
        "bot_identity_phone",
        ["phone_hmac"],
        schema=SCHEMA,
    )
    op.create_table(
        "bot_identity_binding",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "subject_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.bot_identity_subject.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.bot_identity_customer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("trust_state", sa.Text(), nullable=False),
        sa.Column("source_case_ref", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen_sync_id", sa.Text()),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "trust_state in ('trusted','self_declared','operator_approved','disputed','inactive')",
            name="ck_bot_identity_binding_trust_state",
        ),
        sa.UniqueConstraint(
            "subject_id",
            "customer_id",
            "source",
            name="uq_bot_identity_binding_subject_customer_source",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "bot_identity_device",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.bot_identity_customer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_ref", sa.Text(), nullable=False),
        sa.Column("producer", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("serial_enc", sa.Text()),
        sa.Column("serial_last4", sa.Text()),
        sa.Column("location", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen_sync_id", sa.Text()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "customer_id",
            "external_ref",
            name="uq_bot_identity_device_customer_ref",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "bot_identity_override",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("phone_hmac", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "subject_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.bot_identity_subject.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "binding_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.bot_identity_binding.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("set_by_user_id", sa.Integer()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "bot_identity_resolution",
        sa.Column("ref", sa.Text(), primary_key=True),
        sa.Column("phone_hmac", sa.Text(), nullable=False),
        sa.Column(
            "subject_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.bot_identity_subject.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "binding_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.bot_identity_binding.id", ondelete="SET NULL"),
        ),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("conversation_ref", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('exact','ambiguous','not_found','disputed','stale')",
            name="ck_bot_identity_resolution_status",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "bot_disclosure_grant",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "resolution_ref",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.bot_identity_resolution.ref", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.bot_identity_customer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("conversation_ref", sa.Text(), nullable=False),
        sa.Column(
            "disclosure_level",
            sa.Text(),
            nullable=False,
            server_default="full_serial",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for table in (
        "bot_disclosure_grant",
        "bot_identity_resolution",
        "bot_identity_override",
        "bot_identity_device",
        "bot_identity_binding",
        "bot_identity_phone",
        "bot_identity_sync_run",
        "bot_identity_subject",
        "bot_identity_customer",
    ):
        op.drop_table(table, schema=SCHEMA)
