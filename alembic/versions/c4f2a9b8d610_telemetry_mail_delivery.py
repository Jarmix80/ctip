"""Trwałe decyzje pocztowe i kolejka przeniesień, bez zmian danych biznesowych."""

from alembic import op

revision = "c4f2a9b8d610"
down_revision = "a6d9e1f3b520"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Dodaje rejestr UID i indeksy bez usuwania pozyskanej telemetrii."""
    op.execute(
        "CREATE INDEX idx_telemetry_origin_archive_path ON ctip.telemetry_record_origin (source_id, archive_path)"
    )
    op.execute(
        """
        CREATE TABLE ctip.telemetry_mail_delivery (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES ctip.telemetry_source(id),
            folder TEXT NOT NULL,
            uidvalidity TEXT NOT NULL,
            uid BIGINT NOT NULL,
            sha256 TEXT NOT NULL,
            message_id TEXT,
            artifact_id TEXT REFERENCES ctip.telemetry_artifact(id),
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            target_folder TEXT NOT NULL,
            move_status TEXT NOT NULL,
            target_uidvalidity TEXT,
            target_uid BIGINT,
            attempts BIGINT NOT NULL,
            last_error TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT uq_telemetry_mail_uid UNIQUE (source_id, folder, uidvalidity, uid)
        )
    """
    )
    op.execute(
        "CREATE INDEX idx_telemetry_mail_pending ON ctip.telemetry_mail_delivery (source_id, move_status)"
    )
    op.execute("CREATE INDEX idx_telemetry_mail_hash ON ctip.telemetry_mail_delivery (sha256)")


def downgrade() -> None:
    """Chroni decyzje i niepotwierdzone przeniesienia przed utratą przy rollbacku."""
    raise RuntimeError("Usunięcie rejestru wiadomości wymaga osobnej zatwierdzonej procedury.")
