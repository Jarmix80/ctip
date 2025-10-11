"""Testy zgodności schematu bazy danych CTIP."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from dotenv import load_dotenv

env_path = Path(".env")
if env_path.exists():
    load_dotenv(env_path, override=False)


def _connect():
    host = os.getenv("PGHOST", "192.168.0.8")
    port = int(os.getenv("PGPORT", "5433"))
    db = os.getenv("PGDATABASE", "ctip")
    user = os.getenv("PGUSER", "appuser")
    password = os.getenv("PGPASSWORD", "change_me")
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
