import unittest

import collector_full


class SmsEnqueueTests(unittest.TestCase):
    """Testy wstawiania SMS do kolejki IVR."""

    class _FakeCursor:
        def __init__(self):
            self.executed: list[tuple[str, tuple]] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params):
            self.executed.append((query, params))

    class _FakeConn:
        def __init__(self):
            self.cursor_instance = SmsEnqueueTests._FakeCursor()

        def cursor(self):
            return self.cursor_instance

    def test_enqueue_sms_casts_jsonb_fields(self):
        conn = self._FakeConn()
        collector_full.enqueue_sms(conn, 7, "123", "Test", ext="500", digit=9)

        self.assertEqual(len(conn.cursor_instance.executed), 1)
        query, params = conn.cursor_instance.executed[0]
        self.assertIn("'ext', %s::text", query)
        self.assertIn("'digit', %s::int", query)
        self.assertEqual(params, ("123", "Test", 7, "500", 9))

    def test_enqueue_sms_skips_empty_destination(self):
        conn = self._FakeConn()
        collector_full.enqueue_sms(conn, 7, "", "Test", ext="500", digit=9)

        self.assertEqual(conn.cursor_instance.executed, [])


if __name__ == "__main__":
    unittest.main()
