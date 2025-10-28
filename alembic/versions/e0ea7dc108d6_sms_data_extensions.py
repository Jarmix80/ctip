"""sms data extensions

Revision ID: e0ea7dc108d6
Revises: 7615e8207b7a
Create Date: 2025-10-11 02:39:22.404407

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e0ea7dc108d6"
down_revision: str | Sequence[str] | None = "7615e8207b7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sms_template",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False, server_default="user"),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
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
        sa.CheckConstraint("scope in ('global','user','contact')", name="sms_template_scope_check"),
        sa.PrimaryKeyConstraint("id"),
        schema="ctip",
    )

    op.create_index(
        "idx_sms_template_scope",
        "sms_template",
        ["scope", "owner_id", "is_active"],
        unique=False,
        schema="ctip",
    )

    op.add_column(
        "sms_out",
        sa.Column("template_id", sa.Integer(), nullable=True),
        schema="ctip",
    )

    op.drop_constraint("sms_out_call_id_fkey", "sms_out", type_="foreignkey", schema="ctip")
    op.create_foreign_key(
        "sms_out_call_id_fkey",
        "sms_out",
        "calls",
        local_cols=["call_id"],
        remote_cols=["id"],
        source_schema="ctip",
        referent_schema="ctip",
        ondelete="SET NULL",
    )

    op.create_foreign_key(
        "sms_out_template_id_fkey",
        "sms_out",
        "sms_template",
        local_cols=["template_id"],
        remote_cols=["id"],
        source_schema="ctip",
        referent_schema="ctip",
        ondelete="SET NULL",
    )

    op.create_unique_constraint(
        "uq_contact_number",
        "contact",
        ["number"],
        schema="ctip",
    )


def downgrade() -> None:
    op.drop_constraint("uq_contact_number", "contact", schema="ctip")

    op.drop_constraint("sms_out_template_id_fkey", "sms_out", schema="ctip")
    op.drop_constraint("sms_out_call_id_fkey", "sms_out", schema="ctip")
    op.create_foreign_key(
        "sms_out_call_id_fkey",
        "sms_out",
        "calls",
        local_cols=["call_id"],
        remote_cols=["id"],
        source_schema="ctip",
        referent_schema="ctip",
    )

    op.drop_column("sms_out", "template_id", schema="ctip")

    op.drop_index("idx_sms_template_scope", table_name="sms_template", schema="ctip")
    op.drop_table("sms_template", schema="ctip")
