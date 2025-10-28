"""add auto ivr sms mapping

Revision ID: 15989372b89d
Revises: b446620b9fe5
Create Date: 2025-10-28 15:44:29.642668

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "15989372b89d"
down_revision: str | Sequence[str] | None = "b446620b9fe5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DEFAULT_SMS_TEXT = (
    "Instrukcja instalacji aplikacji Ksero Partner znajdziesz na stronie "
    "https://www.ksero-partner.com.pl/appkp/."
)


def upgrade() -> None:
    """Dodaje unikalne mapowanie IVR i domyślną wiadomość dla numeru 500."""
    # Usuń duplikaty przed nałożeniem ograniczenia.
    op.execute(
        """
        DELETE FROM ctip.ivr_map a
        USING ctip.ivr_map b
        WHERE a.ext = b.ext AND a.ctid > b.ctid
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_ivr_map_ext'
                  AND conrelid = 'ctip.ivr_map'::regclass
            ) THEN
                ALTER TABLE ctip.ivr_map ADD CONSTRAINT uq_ivr_map_ext UNIQUE (ext);
            END IF;
        END;
        $$
        """
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO ctip.ivr_map (digit, ext, sms_text, enabled)
            VALUES (9, '500', :sms_text, TRUE)
            ON CONFLICT (ext) DO UPDATE
            SET digit = EXCLUDED.digit,
                sms_text = EXCLUDED.sms_text,
                enabled = EXCLUDED.enabled
            """
        ),
        {"sms_text": DEFAULT_SMS_TEXT},
    )


def downgrade() -> None:
    op.execute("DELETE FROM ctip.ivr_map WHERE ext = '500'")
    op.execute(
        """
        ALTER TABLE ctip.ivr_map DROP CONSTRAINT IF EXISTS uq_ivr_map_ext
        """
    )
