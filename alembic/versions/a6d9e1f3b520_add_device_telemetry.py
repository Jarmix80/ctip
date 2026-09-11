"""Dodanie historii źródeł telemetrycznych bez zmian w tabelach biznesowych."""

from alembic import op

revision = "a6d9e1f3b520"
down_revision = "f2b7c9d4e6a1"
branch_labels = None
depends_on = None

STATEMENTS = [
    "CREATE TABLE ctip.telemetry_source (\n\tid TEXT NOT NULL, \n\tname TEXT NOT NULL, \n\tkind TEXT NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tcheckpoint JSONB NOT NULL, \n\tlast_success_at TIMESTAMP WITH TIME ZONE, \n\tlast_error TEXT, \n\tPRIMARY KEY (id), \n\tUNIQUE (name)\n)",
    "CREATE TABLE ctip.telemetry_import (\n\tid TEXT NOT NULL, \n\tsource_id TEXT NOT NULL, \n\tlocator TEXT NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tfinished_at TIMESTAMP WITH TIME ZONE, \n\tstatus TEXT NOT NULL, \n\tcounts JSONB NOT NULL, \n\terror_code TEXT, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(source_id) REFERENCES ctip.telemetry_source (id)\n)",
    "CREATE TABLE ctip.telemetry_artifact (\n\tid TEXT NOT NULL, \n\tsha256 TEXT NOT NULL, \n\tsize_bytes BIGINT NOT NULL, \n\tmedia_type TEXT NOT NULL, \n\tcontent_gzip BYTEA, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (sha256)\n)",
    "CREATE TABLE ctip.telemetry_device_link (\n\tid TEXT NOT NULL, \n\tsource_id TEXT NOT NULL, \n\texternal_key TEXT NOT NULL, \n\tserial TEXT, \n\tms_machine_id BIGINT, \n\tms_customer_id BIGINT, \n\tstatus TEXT NOT NULL, \n\tvalid_from TIMESTAMP WITH TIME ZONE NOT NULL, \n\tvalid_to TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(source_id) REFERENCES ctip.telemetry_source (id)\n)",
    "CREATE TABLE ctip.telemetry_record (\n\tid TEXT NOT NULL, \n\tsource_id TEXT NOT NULL, \n\texternal_key TEXT NOT NULL, \n\trevision_hash TEXT NOT NULL, \n\tparser_version TEXT NOT NULL, \n\tsemantic_key TEXT NOT NULL, \n\tkind TEXT NOT NULL, \n\tserial TEXT, \n\tobserved_at TIMESTAMP WITH TIME ZONE, \n\ttime_precision TEXT NOT NULL, \n\ttime_basis TEXT NOT NULL, \n\treceived_at TIMESTAMP WITH TIME ZONE, \n\timported_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tpayload JSONB NOT NULL, \n\tmeasurements JSONB NOT NULL, \n\tdevice_link_id TEXT, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_telemetry_record_revision UNIQUE (source_id, external_key, revision_hash, parser_version), \n\tFOREIGN KEY(source_id) REFERENCES ctip.telemetry_source (id), \n\tFOREIGN KEY(device_link_id) REFERENCES ctip.telemetry_device_link (id)\n)",
    "CREATE TABLE ctip.telemetry_record_origin (\n\tid TEXT NOT NULL, \n\tsource_id TEXT NOT NULL, \n\timport_id TEXT NOT NULL, \n\tartifact_id TEXT, \n\trecord_id TEXT, \n\tlocator TEXT NOT NULL, \n\tposition TEXT NOT NULL, \n\tfingerprint TEXT NOT NULL, \n\tarchive_path TEXT, \n\tarchive_status TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_telemetry_origin UNIQUE (source_id, locator, position, fingerprint), \n\tFOREIGN KEY(source_id) REFERENCES ctip.telemetry_source (id), \n\tFOREIGN KEY(import_id) REFERENCES ctip.telemetry_import (id), \n\tFOREIGN KEY(artifact_id) REFERENCES ctip.telemetry_artifact (id), \n\tFOREIGN KEY(record_id) REFERENCES ctip.telemetry_record (id)\n)",
    "CREATE TABLE ctip.telemetry_quality_issue (\n\tid TEXT NOT NULL, \n\trecord_id TEXT NOT NULL, \n\tcode TEXT NOT NULL, \n\tmetric TEXT NOT NULL, \n\trelated_record_id TEXT, \n\tdetails JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_telemetry_issue UNIQUE (record_id, code, metric), \n\tFOREIGN KEY(record_id) REFERENCES ctip.telemetry_record (id), \n\tFOREIGN KEY(related_record_id) REFERENCES ctip.telemetry_record (id)\n)",
    "CREATE INDEX idx_telemetry_import_source ON ctip.telemetry_import (source_id, started_at)",
    "CREATE INDEX idx_telemetry_device_identity ON ctip.telemetry_device_link (source_id, external_key)",
    "CREATE INDEX idx_telemetry_record_key ON ctip.telemetry_record (source_id, external_key)",
    "CREATE INDEX idx_telemetry_record_semantic ON ctip.telemetry_record (semantic_key)",
    "CREATE INDEX idx_telemetry_record_series ON ctip.telemetry_record (source_id, serial, observed_at)",
    "CREATE INDEX idx_telemetry_origin_archive ON ctip.telemetry_record_origin (archive_status)",
]


def upgrade() -> None:
    """Tworzy siedem tabel i indeksy pochodzenia oraz kontroli jakości."""
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Chroni pozyskaną historię przed automatycznym usunięciem przy rollbacku kodu."""
    raise RuntimeError("Historia telemetrii wymaga osobnej, zatwierdzonej procedury usunięcia.")
