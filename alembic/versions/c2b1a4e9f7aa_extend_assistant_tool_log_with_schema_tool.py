"""extend assistant tool log with schema tool

Revision ID: c2b1a4e9f7aa
Revises: a9f5c8e7d2b1
Create Date: 2026-04-30 18:25:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2b1a4e9f7aa"
down_revision: str | Sequence[str] | None = "a9f5c8e7d2b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"
TABLE = "assistant_tool_call_log"
CONSTRAINT = "assistant_tool_call_log_tool_name_check"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        "tool_name in ('firebird_read','sheets_read','imap_read','ctip_schema_read')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        "tool_name in ('firebird_read','sheets_read','imap_read')",
        schema=SCHEMA,
    )
