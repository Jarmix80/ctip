"""Wspólne dowody zdarzeń ORBIT i audytowalne ustawienia ostrzeżeń."""

from alembic import op

revision = "d9a4f6b8c031"
down_revision = "c8f3e5a7b920"
branch_labels = None
depends_on = None

STATEMENTS = (
    """
    CREATE TABLE ctip.shipping_orbit_evidence (
        event_id TEXT NOT NULL,
        record_id TEXT NOT NULL,
        role TEXT NOT NULL,
        current BOOLEAN NOT NULL,
        PRIMARY KEY (event_id, record_id),
        FOREIGN KEY(event_id) REFERENCES ctip.shipping_orbit_event (id),
        FOREIGN KEY(record_id) REFERENCES ctip.telemetry_record (id)
    )
    """,
    """
    CREATE TABLE ctip.shipping_orbit_policy (
        scope TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        color TEXT NOT NULL,
        "values" JSON NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_by INTEGER,
        PRIMARY KEY (scope, scope_id, color),
        CONSTRAINT orbit_policy_scope CHECK (scope IN ('global','customer','device')),
        CONSTRAINT orbit_policy_color CHECK (color IN ('all','black','cyan','magenta','yellow')),
        FOREIGN KEY(updated_by) REFERENCES ctip.admin_user (id)
    )
    """,
)


def upgrade():
    """Dodaje tabele bez zmiany historii i źródeł."""
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade():
    """Wyłączenie funkcji nie uprawnia do usunięcia historii dowodów i polityk."""
    raise RuntimeError("Wyłącz ORBIT bez usuwania dowodów i ustawień.")
