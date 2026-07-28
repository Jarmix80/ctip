"""Rozszerz projekcję urządzeń i wielokrotny wybór w sprawach.

Revision ID: a6f3c8d2e910
Revises: e2b7c4d9a610
Create Date: 2026-07-28 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "a6f3c8d2e910"
down_revision: str | Sequence[str] | None = "e2b7c4d9a610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"


def upgrade() -> None:
    """Dodaje bezpieczne referencje, zdjęcia i listę urządzeń sprawy."""
    op.add_column(
        "bot_identity_device",
        sa.Column("device_ref", sa.Text()),
        schema=SCHEMA,
    )
    op.add_column(
        "bot_identity_device",
        sa.Column("image_url", sa.Text()),
        schema=SCHEMA,
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(f"SELECT id FROM {SCHEMA}.bot_identity_device WHERE device_ref IS NULL")
    ).fetchall()
    if rows:
        connection.execute(
            sa.text(
                f"UPDATE {SCHEMA}.bot_identity_device "
                "SET device_ref = :device_ref WHERE id = :id"
            ),
            [{"id": row.id, "device_ref": str(uuid4())} for row in rows],
        )
    op.alter_column(
        "bot_identity_device",
        "device_ref",
        nullable=False,
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_bot_identity_device_ref",
        "bot_identity_device",
        ["device_ref"],
        schema=SCHEMA,
    )
    op.add_column(
        "crm_case",
        sa.Column("device_refs", sa.JSON()),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Usuwa rozszerzenia kontraktu urządzeń."""
    op.drop_column("crm_case", "device_refs", schema=SCHEMA)
    op.drop_constraint(
        "uq_bot_identity_device_ref",
        "bot_identity_device",
        type_="unique",
        schema=SCHEMA,
    )
    op.drop_column("bot_identity_device", "image_url", schema=SCHEMA)
    op.drop_column("bot_identity_device", "device_ref", schema=SCHEMA)
