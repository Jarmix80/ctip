"""merge admin and contact revisions

Revision ID: b446620b9fe5
Revises: 27b84e77c2a0, 2a3e1f8b0869
Create Date: 2025-10-28 15:27:12.087066

"""

from collections.abc import Sequence

from alembic import op

CHECK_INDEX_EXISTS = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'ctip'
          AND c.relname = 'idx_contact_firebird_id'
    ) THEN
        CREATE INDEX idx_contact_firebird_id ON ctip.contact (firebird_id);
    END IF;
END;
$$
"""


# revision identifiers, used by Alembic.
revision: str = "b446620b9fe5"
down_revision: str | Sequence[str] | None = ("27b84e77c2a0", "2a3e1f8b0869")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Zapewnia zgodność schematu po scaleniu migracji."""
    op.execute("ALTER TABLE ctip.contact ADD COLUMN IF NOT EXISTS firebird_id TEXT")
    op.execute(CHECK_INDEX_EXISTS)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ctip.idx_contact_firebird_id")
    op.execute("ALTER TABLE ctip.contact DROP COLUMN IF EXISTS firebird_id")
