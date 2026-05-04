"""add worker key to assistant chat thread

Revision ID: b8c3e2d91a7f
Revises: 8d7a3b9e4c11
Create Date: 2026-05-04 10:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8c3e2d91a7f"
down_revision: str | Sequence[str] | None = "8d7a3b9e4c11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"
TABLE = "assistant_chat_thread"
CONSTRAINT = "assistant_chat_thread_worker_key_check"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(
            "worker_key",
            sa.Text(),
            nullable=False,
            server_default="ksero_partner_analyst",
        ),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        "worker_key in ('ksero_partner_analyst','opiekun_klienta','diagnosta_bazy_ms')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, schema=SCHEMA, type_="check")
    op.drop_column(TABLE, "worker_key", schema=SCHEMA)
