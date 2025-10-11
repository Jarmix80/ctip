import time
import unittest

import collector_full


class CTIPHandshakeTest(unittest.TestCase):
    def setUp(self):
        self.client = collector_full.CTIPClient()
        self.sent_lines: list[str] = []

        def _fake_send_line(line: str):
            self.sent_lines.append(line)

        self.client.send_line = _fake_send_line  # type: ignore[assignment]
        self.client.sock = object()

        self._orig_pin = collector_full.PBX_PIN
        collector_full.PBX_PIN = "1234"
        self.addCleanup(self._restore_pin)

    def _restore_pin(self):
        collector_full.PBX_PIN = self._orig_pin

    def test_identity_response_triggers_loga(self):
        self.client.start_handshake()
        self.sent_lines.clear()

        self.client.handle_control_packet(
            "AOK",
            ["aOK", "NCP-000", "NO03914", "v1.23.0140/15", "2025.10.10", "01:54'59"],
        )

        self.assertTrue(self.client.awaiting_login_ack)
        self.assertIn("aLOGA 1234", self.sent_lines)

    def test_loga_ack_switches_flags(self):
        self.client.awaiting_login_ack = True
        self.client.login_request_ts = time.monotonic()

        self.client.handle_control_packet("AOK", ["aOK", "LOGA"])

        self.assertTrue(self.client.logged_in)
        self.assertFalse(self.client.awaiting_login_ack)
        self.assertIsNone(self.client.login_request_ts)

    def test_loga_ack_without_keyword_switches_flags(self):
        self.client.awaiting_login_ack = True
        self.client.login_request_ts = time.monotonic()

        self.client.handle_control_packet("AOK", ["aOK", "NCP", "1.23", "15"])

        self.assertTrue(self.client.logged_in)
        self.assertFalse(self.client.awaiting_login_ack)
        self.assertIsNone(self.client.login_request_ts)

    def test_loga_rejection_raises(self):
        self.client.awaiting_login_ack = True
        self.client.login_request_ts = time.monotonic()

        with self.assertRaises(collector_full.CTIPLoginError):
            self.client.handle_control_packet("ANAK", ["aNAK", "LOGA", "INUSE"])

    def test_timeout_without_ack_raises(self):
        self.client.awaiting_login_ack = True
        self.client.login_request_ts = time.monotonic() - (collector_full.LOGIN_ACK_TIMEOUT + 1)
        self.client.logged_in = False

        with self.assertRaises(collector_full.CTIPLoginError):
            self.client.handle_packet(b"RING 201 555123456")


if __name__ == "__main__":
    unittest.main()
