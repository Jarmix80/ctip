import unittest

import collector_full


class SchemaValidationTests(unittest.TestCase):
    """Testy weryfikacji schematu bazy danych."""

    class _FakeCursor:
        def __init__(self, rows):
            self.rows = rows

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query):
            # Zapytanie nie wymaga dodatkowej logiki w testach.
            return None

        def fetchall(self):
            return list(self.rows)

    class _FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def cursor(self):
            return SchemaValidationTests._FakeCursor(self._rows)

    def _rows_from_required(self, *, drop: tuple[str, str] | None = None):
        rows: list[tuple[str, str]] = []
        for table, columns in collector_full.REQUIRED_SCHEMA_COLUMNS.items():
            for column in columns:
                if drop and drop == (table, column):
                    continue
                rows.append((table, column))
        return rows

    def test_verify_required_schema_ok(self):
        conn = self._FakeConn(self._rows_from_required())
        # Nie powinna zostać zgłoszona żadna informacja o błędzie.
        collector_full.verify_required_schema(conn)

    def test_verify_required_schema_missing_column(self):
        missing = ("sms_out", "provider_status")
        conn = self._FakeConn(self._rows_from_required(drop=missing))
        with self.assertRaises(collector_full.SchemaValidationError) as ctx:
            collector_full.verify_required_schema(conn)
        self.assertIn("sms_out", str(ctx.exception))
        self.assertIn("provider_status", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
