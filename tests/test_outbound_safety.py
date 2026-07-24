"""Testy izolacji komunikacji wychodzącej w profilu testowym."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

from app.core.config import settings
from app.services.email_client import send_smtp_message
from app.services.sms_provider import HttpSmsProvider


class DummySmtp:
    """Minimalny lokalny transport SMTP używany w testach."""

    instances: list[DummySmtp] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.messages: list[EmailMessage] = []
        self.instances.append(self)

    def __enter__(self) -> DummySmtp:
        return self

    def __exit__(self, *_args) -> None:
        return None

    def ehlo(self) -> None:
        return None

    def send_message(self, message: EmailMessage) -> None:
        self.messages.append(message)


class OutboundSafetyTests(unittest.TestCase):
    """Waliduje przechwytywanie e-maili i pełną symulację SMS."""

    def setUp(self) -> None:
        self.previous = {
            "ctip_runtime_profile": settings.ctip_runtime_profile,
            "outbound_delivery_mode": settings.outbound_delivery_mode,
            "outbound_audit_dir": settings.outbound_audit_dir,
            "email_host": settings.email_host,
            "email_port": settings.email_port,
        }
        self.temp_dir = tempfile.TemporaryDirectory()
        settings.ctip_runtime_profile = "test"
        settings.outbound_delivery_mode = "capture"
        settings.outbound_audit_dir = self.temp_dir.name
        settings.email_host = "localhost"
        settings.email_port = 1025
        DummySmtp.instances.clear()

    def tearDown(self) -> None:
        for key, value in self.previous.items():
            setattr(settings, key, value)
        self.temp_dir.cleanup()

    @staticmethod
    def _message() -> EmailMessage:
        message = EmailMessage()
        message["From"] = "ctip-test@localhost"
        message["To"] = "klient@example.com"
        message["Subject"] = "Test przechwycenia"
        message.set_content("Pełna treść wiadomości testowej")
        return message

    def test_email_uses_local_mailpit_and_writes_full_report(self) -> None:
        with mock.patch("app.services.email_client.smtplib.SMTP", DummySmtp):
            result = asyncio.run(
                send_smtp_message(
                    host="smtp.production.example",
                    port=587,
                    username="real-user",
                    password="real-password",
                    use_tls=True,
                    use_ssl=False,
                    message=self._message(),
                    source="unit_test",
                )
            )

        self.assertTrue(result.success)
        self.assertEqual(DummySmtp.instances[0].host, "localhost")
        self.assertEqual(DummySmtp.instances[0].port, 1025)
        logs = list(Path(self.temp_dir.name).glob("outbound_test_*.log"))
        self.assertEqual(len(logs), 1)
        entry = json.loads(logs[0].read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(entry["recipients"], ["klient@example.com"])
        self.assertEqual(entry["source"], "unit_test")
        self.assertIn("Pełna treść wiadomości testowej", entry["content"])

    def test_live_mode_is_rejected_in_test_runtime(self) -> None:
        settings.outbound_delivery_mode = "live"
        with mock.patch("app.services.email_client.smtplib.SMTP") as smtp:
            result = asyncio.run(
                send_smtp_message(
                    host="smtp.production.example",
                    port=587,
                    username=None,
                    password=None,
                    use_tls=False,
                    use_ssl=False,
                    message=self._message(),
                )
            )
        self.assertFalse(result.success)
        smtp.assert_not_called()

    def test_sms_test_mode_never_opens_http_connection(self) -> None:
        provider = HttpSmsProvider(
            "https://sms.production.example",
            "token",
            "CTIP",
            test_mode=True,
            delivery_mode="capture",
        )
        provider._client = mock.Mock(side_effect=AssertionError("Połączenie HTTP jest zabronione"))
        result = provider.send_sms(
            "+48123123123",
            "Treść SMS",
            metadata={"sms_id": 10, "source": "unit_test_sms"},
        )
        self.assertTrue(result.success)
        self.assertEqual(result.provider_status, "SIMULATED")
        provider._client.assert_not_called()
        log = next(Path(self.temp_dir.name).glob("outbound_test_*.log"))
        entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(entry["channel"], "sms")
        self.assertEqual(entry["recipients"], ["+48123123123"])
        self.assertEqual(entry["content"], "Treść SMS")


if __name__ == "__main__":
    unittest.main()
