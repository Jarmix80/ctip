"""Testy jednostkowe SQL guard modułu asystenta."""

from __future__ import annotations

import unittest

from app.services.assistant_sql_guard import AssistantSqlGuardError, guard_readonly_sql


class AssistantSqlGuardTests(unittest.TestCase):
    def test_accepts_select_and_wraps_with_row_limit(self) -> None:
        result = guard_readonly_sql("SELECT id, name FROM klient", row_limit=120)
        self.assertIn("SELECT * FROM", result.wrapped_sql)
        self.assertIn("ROWS 120", result.wrapped_sql)
        self.assertEqual(result.normalized_sql, "SELECT id, name FROM klient")

    def test_accepts_cte(self) -> None:
        sql = """
            WITH recent AS (
                SELECT id FROM klient
            )
            SELECT * FROM recent
        """
        result = guard_readonly_sql(sql, row_limit=50)
        self.assertTrue(result.normalized_sql.lower().startswith("with"))
        self.assertIn("ROWS 50", result.wrapped_sql)

    def test_blocks_write_keywords(self) -> None:
        with self.assertRaises(AssistantSqlGuardError):
            guard_readonly_sql("UPDATE klient SET nazwa='X'", row_limit=20)

    def test_blocks_multiple_statements(self) -> None:
        with self.assertRaises(AssistantSqlGuardError):
            guard_readonly_sql("SELECT * FROM klient; SELECT * FROM umowa", row_limit=20)

    def test_blocks_non_select_statement(self) -> None:
        with self.assertRaises(AssistantSqlGuardError):
            guard_readonly_sql("EXECUTE PROCEDURE TEST_PROC", row_limit=20)

    def test_ignores_forbidden_keyword_inside_comment(self) -> None:
        sql = """
            -- delete from klient
            SELECT id FROM klient
        """
        result = guard_readonly_sql(sql, row_limit=10)
        self.assertIn("SELECT id FROM klient", result.wrapped_sql)


if __name__ == "__main__":
    unittest.main()
