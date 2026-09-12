"""Dodanie projekcji ORBIT, wskaźnika wersji telemetrii i uprawnienia finansowego."""

from alembic import op

revision = "c8f3e5a7b920"
down_revision = "b7e2d4f6a810"
branch_labels = None
depends_on = None

STATEMENTS = (
    """
    CREATE TABLE ctip.telemetry_record_head (
        source_id TEXT NOT NULL,
        external_key TEXT NOT NULL,
        record_id TEXT NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (source_id, external_key),
        FOREIGN KEY(source_id) REFERENCES ctip.telemetry_source (id),
        FOREIGN KEY(record_id) REFERENCES ctip.telemetry_record (id)
    )
    """,
    "CREATE INDEX idx_telemetry_record_serial_time ON ctip.telemetry_record (serial, observed_at)",
    """
    CREATE TABLE ctip.shipping_orbit_device (
        id TEXT NOT NULL,
        ms_machine_id INTEGER NOT NULL,
        serial TEXT NOT NULL,
        model TEXT NOT NULL,
        customer TEXT NOT NULL,
        status TEXT NOT NULL,
        data JSON NOT NULL,
        report JSON NOT NULL,
        fingerprint TEXT NOT NULL,
        synced_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT orbit_device_status CHECK (status IN ('active','suspended','scrapped','review')),
        UNIQUE (ms_machine_id)
    )
    """,
    "CREATE INDEX ix_ctip_shipping_orbit_device_serial ON ctip.shipping_orbit_device (serial)",
    "CREATE INDEX ix_ctip_shipping_orbit_device_status ON ctip.shipping_orbit_device (status)",
    """
    CREATE TABLE ctip.shipping_orbit_event (
        id TEXT NOT NULL,
        device_id TEXT NOT NULL,
        record_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        source TEXT NOT NULL,
        observed_at TIMESTAMP WITH TIME ZONE,
        time_precision TEXT NOT NULL,
        contract_id TEXT,
        title TEXT NOT NULL,
        status TEXT NOT NULL,
        data JSON NOT NULL,
        finance JSON NOT NULL,
        current BOOLEAN NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(device_id) REFERENCES ctip.shipping_orbit_device (id),
        FOREIGN KEY(record_id) REFERENCES ctip.telemetry_record (id)
    )
    """,
    "CREATE INDEX idx_orbit_device_contract ON ctip.shipping_orbit_event (device_id, contract_id)",
    "CREATE INDEX idx_orbit_device_time ON ctip.shipping_orbit_event (device_id, observed_at, id)",
    """
    CREATE TABLE ctip.shipping_orbit_run (
        name TEXT NOT NULL,
        finished_at TIMESTAMP WITH TIME ZONE NOT NULL,
        status TEXT NOT NULL,
        counts JSON NOT NULL,
        error_code TEXT,
        PRIMARY KEY (name)
    )
    """,
    "ALTER TABLE ctip.admin_user ADD COLUMN can_view_orbit_finance BOOLEAN NOT NULL DEFAULT false",
)


def upgrade() -> None:
    """Dodaje stałe DDL bez heurystycznego backfillu head i zmian historii źródeł."""
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Blokuje usuwanie projekcji, wskaźników i uprawnień podczas rollbacku kodu."""
    raise RuntimeError("Usunięcie danych ORBIT wymaga osobnej zatwierdzonej procedury.")
