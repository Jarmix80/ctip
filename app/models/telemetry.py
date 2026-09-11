"""Tabele niezmiennej historii źródeł telemetrycznych urządzeń."""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from .base import Base

DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def _table(name: str, *columns) -> Table:
    """Buduje tabelę z identyfikatorem niezależnym od sekwencji źródła."""
    return Table(name, Base.metadata, Column("id", Text, primary_key=True), *columns)


source = _table(
    "telemetry_source",
    Column("name", Text, nullable=False, unique=True),
    Column("kind", Text, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("checkpoint", DOCUMENT, nullable=False),
    Column("last_success_at", DateTime(timezone=True)),
    Column("last_error", Text),
)
imports = _table(
    "telemetry_import",
    Column("source_id", ForeignKey(source.c.id), nullable=False),
    Column("locator", Text, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", Text, nullable=False),
    Column("counts", DOCUMENT, nullable=False),
    Column("error_code", Text),
)
artifact = _table(
    "telemetry_artifact",
    Column("sha256", Text, nullable=False, unique=True),
    Column("size_bytes", BigInteger, nullable=False),
    Column("media_type", Text, nullable=False),
    Column("content_gzip", LargeBinary),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
device_link = _table(
    "telemetry_device_link",
    Column("source_id", ForeignKey(source.c.id), nullable=False),
    Column("external_key", Text, nullable=False),
    Column("serial", Text),
    Column("ms_machine_id", BigInteger),
    Column("ms_customer_id", BigInteger),
    Column("status", Text, nullable=False),
    Column("valid_from", DateTime(timezone=True), nullable=False),
    Column("valid_to", DateTime(timezone=True)),
)
record = _table(
    "telemetry_record",
    Column("source_id", ForeignKey(source.c.id), nullable=False),
    Column("external_key", Text, nullable=False),
    Column("revision_hash", Text, nullable=False),
    Column("parser_version", Text, nullable=False),
    Column("semantic_key", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("serial", Text),
    Column("observed_at", DateTime(timezone=True)),
    Column("time_precision", Text, nullable=False),
    Column("time_basis", Text, nullable=False),
    Column("received_at", DateTime(timezone=True)),
    Column("imported_at", DateTime(timezone=True), nullable=False),
    Column("payload", DOCUMENT, nullable=False),
    Column("measurements", DOCUMENT, nullable=False),
    Column("device_link_id", ForeignKey(device_link.c.id)),
    UniqueConstraint(
        "source_id",
        "external_key",
        "revision_hash",
        "parser_version",
        name="uq_telemetry_record_revision",
    ),
)
origin = _table(
    "telemetry_record_origin",
    Column("source_id", ForeignKey(source.c.id), nullable=False),
    Column("import_id", ForeignKey(imports.c.id), nullable=False),
    Column("artifact_id", ForeignKey(artifact.c.id)),
    Column("record_id", ForeignKey(record.c.id)),
    Column("locator", Text, nullable=False),
    Column("position", Text, nullable=False),
    Column("fingerprint", Text, nullable=False),
    Column("archive_path", Text),
    Column("archive_status", Text, nullable=False),
    UniqueConstraint("source_id", "locator", "position", "fingerprint", name="uq_telemetry_origin"),
)
issue = _table(
    "telemetry_quality_issue",
    Column("record_id", ForeignKey(record.c.id), nullable=False),
    Column("code", Text, nullable=False),
    Column("metric", Text, nullable=False),
    Column("related_record_id", ForeignKey(record.c.id)),
    Column("details", DOCUMENT, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("record_id", "code", "metric", name="uq_telemetry_issue"),
)
Index("idx_telemetry_record_series", record.c.source_id, record.c.serial, record.c.observed_at)
Index("idx_telemetry_record_semantic", record.c.semantic_key)
Index("idx_telemetry_record_key", record.c.source_id, record.c.external_key)
Index("idx_telemetry_import_source", imports.c.source_id, imports.c.started_at)
Index("idx_telemetry_origin_archive", origin.c.archive_status)
Index("idx_telemetry_origin_archive_path", origin.c.source_id, origin.c.archive_path)
Index("idx_telemetry_device_identity", device_link.c.source_id, device_link.c.external_key)
mail_delivery = _table(
    "telemetry_mail_delivery",
    Column("source_id", ForeignKey(source.c.id), nullable=False),
    Column("folder", Text, nullable=False),
    Column("uidvalidity", Text, nullable=False),
    Column("uid", BigInteger, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("message_id", Text),
    Column("artifact_id", ForeignKey(artifact.c.id)),
    Column("decision", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("target_folder", Text, nullable=False),
    Column("move_status", Text, nullable=False),
    Column("target_uidvalidity", Text),
    Column("target_uid", BigInteger),
    Column("attempts", BigInteger, nullable=False, default=0),
    Column("last_error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("source_id", "folder", "uidvalidity", "uid", name="uq_telemetry_mail_uid"),
)
Index("idx_telemetry_mail_pending", mail_delivery.c.source_id, mail_delivery.c.move_status)
Index("idx_telemetry_mail_hash", mail_delivery.c.sha256)
TELEMETRY_TABLES = (source, imports, artifact, device_link, record, origin, issue, mail_delivery)
