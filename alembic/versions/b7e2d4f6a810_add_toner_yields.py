"""Dodanie katalogu wydajności tonerów i odrębnego uprawnienia do korekt."""

from alembic import op

revision = "b7e2d4f6a810"
down_revision = "e8c7d6a5b410"
branch_labels = None
depends_on = None

STATEMENTS = [
    "CREATE TABLE ctip.shipping_toner_yield (\n\titem_id INTEGER NOT NULL, \n\twarehouse_id INTEGER NOT NULL, \n\titem_index TEXT NOT NULL, \n\tname TEXT NOT NULL, \n\tbrand TEXT NOT NULL, \n\tsupplier TEXT NOT NULL, \n\tsku TEXT NOT NULL, \n\tean TEXT NOT NULL, \n\tcolor TEXT NOT NULL, \n\tkind TEXT NOT NULL, \n\tstock NUMERIC(16, 4) NOT NULL, \n\tmodels JSON NOT NULL, \n\tscope TEXT NOT NULL, \n\tscope_note TEXT NOT NULL, \n\tscope_review BOOLEAN NOT NULL, \n\texcluded_model_ids JSON NOT NULL, \n\tpages INTEGER, \n\tstatus TEXT NOT NULL, \n\tsource TEXT NOT NULL, \n\tbasis TEXT NOT NULL, \n\treason TEXT NOT NULL, \n\tmanual_override BOOLEAN NOT NULL, \n\trevision INTEGER NOT NULL, \n\tsynced_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (item_id), \n\tCONSTRAINT toner_yield_status CHECK (status IN ('confirmed','estimated','missing')), \n\tCONSTRAINT toner_yield_scope CHECK (scope IN ('active','inactive','review')), \n\tCONSTRAINT toner_yield_pages CHECK ((status = 'missing' AND pages IS NULL) OR (status <> 'missing' AND pages IS NOT NULL AND pages > 0))\n)",
    "CREATE INDEX ix_ctip_shipping_toner_yield_scope ON ctip.shipping_toner_yield (scope)",
    "CREATE TABLE ctip.shipping_toner_yield_evidence (\n\tid SERIAL NOT NULL, \n\titem_id INTEGER NOT NULL, \n\tfingerprint TEXT NOT NULL, \n\tpayload JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_toner_yield_evidence UNIQUE (item_id, fingerprint), \n\tFOREIGN KEY(item_id) REFERENCES ctip.shipping_toner_yield (item_id)\n)",
    "CREATE INDEX ix_ctip_shipping_toner_yield_evidence_item_id ON ctip.shipping_toner_yield_evidence (item_id)",
    "CREATE TABLE ctip.shipping_toner_yield_change (\n\tid SERIAL NOT NULL, \n\titem_id INTEGER NOT NULL, \n\trevision INTEGER NOT NULL, \n\tactor_id INTEGER, \n\tactor_label TEXT NOT NULL, \n\tbefore JSON NOT NULL, \n\tafter JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_toner_yield_revision UNIQUE (item_id, revision), \n\tFOREIGN KEY(item_id) REFERENCES ctip.shipping_toner_yield (item_id), \n\tFOREIGN KEY(actor_id) REFERENCES ctip.admin_user (id) ON DELETE SET NULL\n)",
    "CREATE INDEX ix_ctip_shipping_toner_yield_change_item_id ON ctip.shipping_toner_yield_change (item_id)",
    "ALTER TABLE ctip.admin_user ADD COLUMN can_edit_toner_yields BOOLEAN NOT NULL DEFAULT false",
]


def upgrade() -> None:
    """Dodaje tabele bez zmiany dokumentów, stanów magazynowych i danych telemetrii."""
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Nie usuwa historii korekt podczas wycofania wersji aplikacji."""
    raise RuntimeError("Usunięcie historii wydajności wymaga osobnej zatwierdzonej procedury.")
