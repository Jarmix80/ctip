"""Testy zgodności schematu bazy danych CTIP."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from dotenv import load_dotenv

env_path = Path(".env.test")
if env_path.exists():
    load_dotenv(env_path, override=False)


def _connect():
    host = os.getenv("PGHOST", "127.0.0.1")
    port = int(os.getenv("PGPORT", "5432"))
    db = os.getenv("PGDATABASE", "ctip_test")
    user = os.getenv("PGUSER", "ctip_test")
    password = os.getenv("PGPASSWORD", "ctip_test")
    return psycopg.connect(host=host, port=port, dbname=db, user=user, password=password)


class DatabaseSchemaTest(unittest.TestCase):
    """Weryfikuje obecność kluczowych tabel, kolumn i ograniczeń schematu `ctip`."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.conn = _connect()
        except Exception as exc:  # pragma: no cover - zależne od środowiska
            raise unittest.SkipTest(f"Brak połączenia z bazą CTIP: {exc}") from None

    @classmethod
    def tearDownClass(cls) -> None:
        cls.conn.close()

    def _fetchall(self, query: str, *params) -> list[tuple]:
        with self.conn.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(query, params)
            return cur.fetchall()

    def test_tables_exist(self) -> None:
        expected_tables = {
            "calls",
            "call_events",
            "sms_out",
            "sms_template",
            "ivr_map",
            "contact",
            "contact_device",
            "shipping_address",
            "shipping_consumable_compatibility",
            "shipping_case",
            "shipping_item",
            "shipping_shipment",
            "shipping_day_close",
            "shipping_event",
        }
        rows = self._fetchall(
            "SELECT table_name FROM information_schema.tables " "WHERE table_schema='ctip'"
        )
        present = {row[0] for row in rows}
        missing = expected_tables - present
        self.assertFalse(missing, f"Brakuje tabel: {', '.join(sorted(missing))}")

    def test_sms_template_columns(self) -> None:
        expected_columns = {
            "id",
            "name",
            "body",
            "scope",
            "owner_id",
            "is_active",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        }
        rows = self._fetchall(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='ctip' AND table_name='sms_template'"
        )
        present = {row[0] for row in rows}
        missing = expected_columns - present
        self.assertFalse(
            missing, f"Tabela sms_template nie ma kolumn: {', '.join(sorted(missing))}"
        )

    def test_shipping_compatibility_columns(self) -> None:
        expected_columns = {
            "firebird_model_id",
            "firebird_warehouse_item_id",
            "status",
            "confidence",
            "evidence",
            "source_hash",
            "first_seen_at",
            "last_seen_at",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "updated_at",
        }
        rows = self._fetchall(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='ctip' AND table_name='shipping_consumable_compatibility'"
        )
        present = {row[0] for row in rows}
        missing = expected_columns - present
        self.assertFalse(
            missing,
            "Tabela shipping_consumable_compatibility nie ma kolumn: " + ", ".join(sorted(missing)),
        )

    def test_shipping_location_guard_columns(self) -> None:
        expected_columns = {
            "location_source",
            "location_text_snapshot",
            "location_fingerprint",
        }
        for table_name in ("shipping_address", "shipping_case"):
            rows = self._fetchall(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='ctip' AND table_name=%s",
                table_name,
            )
            present = {row[0] for row in rows}
            missing = expected_columns - present
            self.assertFalse(
                missing,
                f"Tabela {table_name} nie ma kolumn: {', '.join(sorted(missing))}",
            )

    def test_shipping_case_invoice_required_column(self) -> None:
        rows = self._fetchall(
            "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema='ctip' AND table_name='shipping_case' "
            "AND column_name='invoice_required'"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "NO")
        self.assertIn(str(rows[0][2]).lower(), {"false", "false::boolean"})

    def test_shipping_item_negative_stock_column(self) -> None:
        rows = self._fetchall(
            "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema='ctip' AND table_name='shipping_item' "
            "AND column_name='allow_negative_stock'"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "NO")
        self.assertIn(str(rows[0][2]).lower(), {"false", "false::boolean"})

    def test_shipping_item_price_snapshot_columns(self) -> None:
        rows = self._fetchall(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema='ctip' AND table_name='shipping_item' "
            "AND column_name IN ('catalog_price_net', 'price_source')"
        )
        present = {row[0]: row[1] for row in rows}
        self.assertEqual(present, {"catalog_price_net": "NO", "price_source": "NO"})

    def test_shipping_shipment_firebird_document_columns(self) -> None:
        expected_columns = {
            "firebird_rw_id",
            "firebird_rw_number",
            "firebird_wz_id",
            "firebird_wz_number",
            "firebird_invoice_id",
            "firebird_invoice_number",
        }
        rows = self._fetchall(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='ctip' AND table_name='shipping_shipment'"
        )
        present = {row[0] for row in rows}
        self.assertFalse(expected_columns - present)

    def test_shipping_archive_columns_and_indexes(self) -> None:
        expected_columns = {
            "closed_by",
            "archive_snapshot",
            "archive_search_text",
        }
        rows = self._fetchall(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='ctip' AND table_name='shipping_shipment'"
        )
        present = {row[0] for row in rows}
        self.assertFalse(expected_columns - present)
        index_rows = self._fetchall(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname='ctip' AND tablename='shipping_shipment'"
        )
        indexes = {row[0] for row in index_rows}
        self.assertIn("idx_shipping_shipment_archive_closed", indexes)
        self.assertIn("idx_shipping_shipment_archive_operator", indexes)
        self.assertIn("idx_shipping_shipment_archive_search_trgm", indexes)

    def test_contact_number_unique(self) -> None:
        rows = self._fetchall(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_schema='ctip' AND table_name='contact' "
            "AND constraint_type='UNIQUE'"
        )
        uniques = {row[0] for row in rows}
        self.assertIn("uq_contact_number", uniques)

    def test_sms_out_foreign_keys(self) -> None:
        rows = self._fetchall(
            "SELECT conname, confrelid::regclass::text, confdeltype "
            "FROM pg_constraint "
            "WHERE conrelid = 'ctip.sms_out'::regclass AND contype='f'"
        )
        fk_map = {name: (table, deltype) for name, table, deltype in rows}
        self.assertIn("sms_out_call_id_fkey", fk_map)
        self.assertEqual(fk_map["sms_out_call_id_fkey"], ("ctip.calls", "n"))
        self.assertIn("sms_out_template_id_fkey", fk_map)
        self.assertEqual(fk_map["sms_out_template_id_fkey"], ("ctip.sms_template", "n"))

    def test_sms_template_index(self) -> None:
        rows = self._fetchall(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname='ctip' AND tablename='sms_template'"
        )
        indexes = {row[0] for row in rows}
        self.assertIn("idx_sms_template_scope", indexes)


if __name__ == "__main__":
    unittest.main()
