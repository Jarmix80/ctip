import unittest
from unittest import mock

import psycopg

import sms_sender


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self, cursor_actions):
        self.closed = False
        self._cursor_actions = list(cursor_actions)

    def cursor(self):
        if not self._cursor_actions:
            raise AssertionError("Brak zdefiniowanych akcji cursor().")
        action = self._cursor_actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    def close(self):
        self.closed = True


class SmsSenderReconnectTests(unittest.TestCase):
    def test_reconnects_after_operational_error(self):
        first_conn = FakeConnection([psycopg.OperationalError("boom")])
        second_conn = FakeConnection([FakeCursor([])])
        connect_calls = []

        def connect_func(**_kwargs):
            connect_calls.append(True)
            return first_conn if len(connect_calls) == 1 else second_conn

        with mock.patch.object(sms_sender, "log_event"):
            sms_sender.run_sender_loop(
                connect_func=connect_func,
                sms_provider=mock.Mock(),
                poll_sec=0,
                sleep_fn=lambda *_args, **_kwargs: None,
                max_cycles=2,
            )

        self.assertEqual(len(connect_calls), 2)
        self.assertTrue(first_conn.closed)


if __name__ == "__main__":
    unittest.main()
