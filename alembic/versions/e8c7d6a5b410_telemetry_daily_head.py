"""Bieżąca wersja migawki dziennej bez powielania niezmiennej historii."""

from alembic import op

revision = "e8c7d6a5b410"
down_revision = "c4f2a9b8d610"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Dodaje jednoznaczny wskaźnik aktualnej wersji, nie modyfikując danych źródłowych."""
    op.execute(
        """
        CREATE TABLE ctip.telemetry_daily_head (
            source_id TEXT NOT NULL REFERENCES ctip.telemetry_source(id),
            external_key TEXT NOT NULL,
            record_id TEXT NOT NULL REFERENCES ctip.telemetry_record(id),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (source_id, external_key)
        )
        """
    )
    op.execute(
        """
        INSERT INTO ctip.telemetry_daily_head (source_id, external_key, record_id, updated_at)
        SELECT DISTINCT ON (source_id, external_key) source_id, external_key, id, imported_at
        FROM ctip.telemetry_record WHERE kind='daily_snapshot'
        ORDER BY source_id, external_key, imported_at DESC, id DESC
        """
    )


def downgrade() -> None:
    """Wymaga osobnej procedury zamiast utraty informacji o korektach źródła."""
    raise RuntimeError("Usunięcie wskaźników migawek wymaga osobnej zatwierdzonej procedury.")
