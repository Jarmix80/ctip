"""extend assistant tool log with workflow devices audit

Revision ID: e7f0a1b2c3d4
Revises: b8c3e2d91a7f
Create Date: 2026-05-20 01:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f0a1b2c3d4"
down_revision: str | Sequence[str] | None = "b8c3e2d91a7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"
TABLE = "assistant_tool_call_log"
CONSTRAINT = "assistant_tool_call_log_tool_name_check"


UPGRADE_TOOLS = (
    "'firebird_read','firebird_business_read','firebird_knowledge_read',"
    "'workflow_devices_audit','sheets_read','imap_read','ctip_schema_read','email_send_report'"
)
DOWNGRADE_TOOLS = (
    "'firebird_read','firebird_business_read','firebird_knowledge_read',"
    "'sheets_read','imap_read','ctip_schema_read'"
)


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        f"tool_name in ({UPGRADE_TOOLS})",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        f"tool_name in ({DOWNGRADE_TOOLS})",
        schema=SCHEMA,
    )
